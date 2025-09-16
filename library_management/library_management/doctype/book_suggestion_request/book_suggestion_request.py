# Copyright (c) 2025, aaditya and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class BookSuggestionRequest(Document):
    # -- Centralized Workflow Configuration --
    WORKFLOW_CONFIG = {
        "Pending for Duplication Check": {
            "approver_field": "librarian",
            "task_description": "Duplication Check",
            "permissions": {"read": 1, "write": 1, "submit": 1, "share": 1},
            "previous_approver_field": None,
        },
        "Pending for HOD Approval": {
            "approver_field": "hod",
            "task_description": "HOD Approval", 
            "permissions": {"read": 1, "write": 0, "submit": 1, "share": 1},
            "previous_approver_field": "librarian",
        },
        "Pending for Library Convener Approval": {
            "approver_field": "library_convener",
            "task_description": "Library Convener Approval",
            "permissions": {"read": 1, "write": 0, "submit": 1, "share": 1},
            "previous_approver_field": "hod",
        },
        "Approved": {"previous_approver_field": "library_convener"},
        "Rejected": {"previous_approver_field": "library_convener"},
    }

    def before_validate(self):
        """Set default approvers from Library Settings"""
        try:
            settings = frappe.get_single("Library Settings")
            self.librarian = settings.default_librarian
            self.library_convener = settings.default_library_convener
        except frappe.DoesNotExistError:
            frappe.throw("Please configure 'Library Settings' first.")

    def before_save(self):
        """Track the previous workflow state before save"""
        if not self.is_new():
            # Get the current document from database to compare states
            current_doc = frappe.get_doc(self.doctype, self.name)
            self._previous_workflow_state = current_doc.workflow_state
        else:
            self._previous_workflow_state = None

    def on_submit(self):
        """Handle workflow on document submission"""
        self._handle_workflow()

    def on_update_after_submit(self):
        """Handle workflow on document update after submission"""  
        self._handle_workflow()

    def _handle_workflow(self):
        """Processes the current workflow state using the config map."""
        # Only process if workflow state has actually changed
        if hasattr(self, '_previous_workflow_state') and self._previous_workflow_state == self.workflow_state:
            return  # No state change, skip processing
            
        state_config = self.WORKFLOW_CONFIG.get(self.workflow_state)
        if not state_config:
            return

        # Remove permissions from previous approver
        prev_approver_field = state_config.get("previous_approver_field")
        if prev_approver_field:
            prev_user = self.get(prev_approver_field)
            if prev_user:
                self._remove_share_permission(prev_user)

        # Assign to the next approver only if state actually changed
        next_approver_field = state_config.get("approver_field")
        if next_approver_field:
            next_user = self.get(next_approver_field)
            permissions = state_config.get("permissions", {"read": 1})
            if next_user:
                self._share_and_assign(
                    user=next_user,
                    task_description=state_config.get("task_description"),
                    permissions=permissions
                )

    def _share_and_assign(self, user, task_description, permissions):
        """Helper to grant permissions and create a ToDo."""
        if not user:
            return
            
        # Use frappe.share.add with correct parameters
        frappe.share.add(
            doctype=self.doctype,
            name=self.name,
            user=user,
            read=permissions.get("read", 0),
            write=permissions.get("write", 0),
            submit=permissions.get("submit", 0),
            share=permissions.get("share", 0)
        )
        
        # Check if ToDo already exists for this user and document
        existing_todo = frappe.db.exists("ToDo", {
            "reference_type": self.doctype,
            "reference_name": self.name,
            "allocated_to": user,
            "status": "Open"
        })
        
        # Only create ToDo if it doesn't exist
        if not existing_todo:
            todo = frappe.new_doc("ToDo")
            todo.allocated_to = user
            todo.reference_type = self.doctype
            todo.reference_name = self.name
            todo.description = f"Please review for {task_description}: {self.name}"
            todo.status = "Open"
            todo.insert(ignore_permissions=True)

    def _remove_share_permission(self, user):
        """Helper to remove sharing permissions from a user."""
        if not user:
            return
            
        try:
            frappe.share.remove(self.doctype, self.name, user, ignore_permissions=True)
            
            # Also close any open ToDos for this user and document
            open_todos = frappe.get_all("ToDo", {
                "reference_type": self.doctype,
                "reference_name": self.name,
                "allocated_to": user,
                "status": "Open"
            })
            
            for todo in open_todos:
                todo_doc = frappe.get_doc("ToDo", todo.name)
                todo_doc.status = "Closed"
                todo_doc.save(ignore_permissions=True)
                
        except Exception:
            # If removal fails, it might not have been shared
            pass
