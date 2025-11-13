import frappe
from frappe import _
from frappe.utils import get_fullname, now_datetime
import json


# Store previous workflow state to detect changes
_previous_states = {}


def check_workflow_state_change(doc, method):
	"""Store the previous workflow state before update"""
	key = f"{doc.doctype}:{doc.name}"
	
	# Get current state from either workflow_state or status field
	current_state = doc.get("workflow_state") or doc.get("status")
	
	# Store previous state if document exists
	if doc.name and frappe.db.exists(doc.doctype, doc.name):
		old_doc = frappe.get_doc(doc.doctype, doc.name)
		previous_state = old_doc.get("workflow_state") or old_doc.get("status")
		_previous_states[key] = previous_state
	else:
		_previous_states[key] = None


def handle_workflow_transition(doc, method):
	"""Handle workflow state transitions and send notifications"""
	try:
		# Get current and previous states
		key = f"{doc.doctype}:{doc.name}"
		previous_state = _previous_states.get(key)
		current_state = doc.get("workflow_state") or doc.get("status")
		
		# Check if state actually changed
		if not current_state or current_state == previous_state:
			return
		
		# Get workflow document
		workflow = get_workflow_for_doctype(doc.doctype)
		if not workflow:
			return
		
		# Get workflow state document
		current_state_doc = get_workflow_state(current_state, workflow.name)
		if not current_state_doc:
			return
		
		# Get recipients
		recipients = get_notification_recipients(doc, workflow, current_state, previous_state)
		
		if recipients:
			# Send notifications
			send_workflow_notifications(
				doc=doc,
				workflow=workflow,
				current_state=current_state,
				previous_state=previous_state,
				recipients=recipients
			)
		
		# Clear stored state
		if key in _previous_states:
			del _previous_states[key]
			
	except Exception as e:
		frappe.log_error(f"Error in workflow notification: {str(e)}", "Workflow Notification Error")


def get_workflow_for_doctype(doctype):
	"""Get the active workflow for a doctype"""
	try:
		workflow = frappe.get_all(
			"Workflow",
			filters={"document_type": doctype, "is_active": 1},
			fields=["name"],
			limit=1
		)
		if workflow:
			return frappe.get_doc("Workflow", workflow[0].name)
		return None
	except Exception:
		return None


def get_workflow_state(state_name, workflow_name):
	"""Get workflow state document"""
	try:
		state = frappe.get_all(
			"Workflow State",
			filters={"workflow": workflow_name, "state": state_name},
			fields=["name"],
			limit=1
		)
		if state:
			return frappe.get_doc("Workflow State", state[0].name)
		return None
	except Exception:
		return None


def get_notification_recipients(doc, workflow, current_state, previous_state):
	"""Get all recipients who should receive notifications"""
	recipients = set()
	
	# 1. Add initiator (the person who created the document)
	initiator = doc.get("owner") or frappe.session.user
	if initiator:
		recipients.add(initiator)
	
	# 2. Get recipients from workflow transitions (allowed roles)
	transition_recipients = get_transition_recipients(doc, workflow, current_state, previous_state)
	recipients.update(transition_recipients)
	
	# 3. Get recipients from custom_extra_notification_recipients_ child table
	extra_recipients = get_extra_notification_recipients(doc)
	recipients.update(extra_recipients)
	
	# Filter out None and empty values
	recipients = {r for r in recipients if r}
	
	return list(recipients)


def get_transition_recipients(doc, workflow, current_state, previous_state):
	"""Get recipients from workflow transitions based on allowed roles"""
	recipients = set()
	
	try:
		# Get transitions that lead to the current state
		# Transitions have from_state and state (to_state)
		transitions = frappe.get_all(
			"Workflow Transition",
			filters={
				"parent": workflow.name,
				"state": current_state  # state is the "to" state
			},
			fields=["allowed", "role", "state", "action"]
		)
		
		# If no transitions found with state field, try alternative structure
		if not transitions:
			# Try getting all transitions and filter
			all_transitions = frappe.get_all(
				"Workflow Transition",
				filters={"parent": workflow.name},
				fields=["allowed", "role", "state", "action", "next_state"]
			)
			
			for trans in all_transitions:
				# Check if this transition leads to current state
				to_state = trans.get("state") or trans.get("next_state")
				if to_state == current_state:
					transitions.append(trans)
		
		for transition in transitions:
			# Get role from allowed field or role field
			role = transition.get("allowed") or transition.get("role")
			
			if role:
				# Get users with this role, but be careful with Employee role
				users = get_users_for_role(role, doc)
				recipients.update(users)
		
	except Exception as e:
		frappe.log_error(f"Error getting transition recipients: {str(e)}", "Workflow Notification Error")
	
	return recipients


def get_users_for_role(role, doc):
	"""Get users for a role, with special handling for Employee role"""
	users = set()
	
	try:
		# If it's Employee role, be more specific
		# Only get users who are directly assigned or have specific relationship to the document
		if role == "Employee":
			# Try to get specific employee from document
			employee_field = None
			
			# Check common employee fields
			for field in ["employee", "assigned_to", "owner", "created_by"]:
				if hasattr(doc, field) and doc.get(field):
					employee_field = doc.get(field)
					break
			
			# If we have a specific employee, get that user
			if employee_field:
				# Check if it's a user or employee link
				if frappe.db.exists("User", employee_field):
					users.add(employee_field)
				elif frappe.db.exists("Employee", employee_field):
					# Get user linked to employee
					emp_doc = frappe.get_doc("Employee", employee_field)
					if emp_doc.user_id:
						users.add(emp_doc.user_id)
			
			# Also add the document owner if they have Employee role
			if doc.get("owner"):
				user_roles = frappe.get_roles(doc.get("owner"))
				if "Employee" in user_roles:
					users.add(doc.get("owner"))
		else:
			# For other roles, get all users with that role
			users_with_role = frappe.get_all(
				"Has Role",
				filters={"role": role, "parenttype": "User"},
				fields=["parent"],
				pluck="parent"
			)
			users.update(users_with_role)
		
	except Exception as e:
		frappe.log_error(f"Error getting users for role {role}: {str(e)}", "Workflow Notification Error")
	
	return users


def get_extra_notification_recipients(doc):
	"""Get recipients from custom_extra_notification_recipients_ child table"""
	recipients = set()
	
	try:
		# Check if the child table exists
		child_table_field = "custom_extra_notification_recipients_"
		
		if hasattr(doc, child_table_field) and doc.get(child_table_field):
			for row in doc.get(child_table_field):
				role = row.get("role")
				if role:
					users = get_users_for_role(role, doc)
					recipients.update(users)
		
	except Exception as e:
		frappe.log_error(f"Error getting extra notification recipients: {str(e)}", "Workflow Notification Error")
	
	return recipients


def send_workflow_notifications(doc, workflow, current_state, previous_state, recipients):
	"""Send email notifications to all recipients"""
	if not recipients:
		return
	
	try:
		# Prepare notification content
		subject = _("Workflow Transition: {0} - {1}").format(
			doc.doctype,
			doc.name
		)
		
		# Get document link
		doc_link = frappe.utils.get_url_to_form(doc.doctype, doc.name)
		
		message = _("""
			<p>The document <strong>{0}</strong> ({1}) has transitioned in the workflow.</p>
			<p><strong>Previous State:</strong> {2}</p>
			<p><strong>Current State:</strong> {3}</p>
			<p><strong>Workflow:</strong> {4}</p>
			<p><a href="{5}">View Document</a></p>
			<p>Please review the document for any required actions.</p>
		""").format(
			doc.name,
			doc.doctype,
			previous_state or _("Initial"),
			current_state,
			workflow.workflow_name,
			doc_link
		)
		
		# Filter recipients - remove current user and invalid emails
		valid_recipients = []
		for recipient in recipients:
			if recipient == frappe.session.user:
				continue
			
			# Get user email
			user_email = frappe.db.get_value("User", recipient, "email")
			if user_email and frappe.utils.validate_email_address(user_email, throw=False):
				valid_recipients.append(user_email)
		
		if not valid_recipients:
			return
		
		# Send email using frappe.sendmail
		try:
			frappe.sendmail(
				recipients=valid_recipients,
				subject=subject,
				message=message,
				reference_doctype=doc.doctype,
				reference_name=doc.name,
				delayed=False,
				now=True
			)
		except Exception as e:
			frappe.log_error(f"Error sending email: {str(e)}", "Workflow Notification Error")
		
		# Also create system notifications for each recipient user
		for recipient in recipients:
			if recipient == frappe.session.user:
				continue
			
			try:
				# Create system notification
				frappe.publish_realtime(
					event="notification",
					message={
						"type": "alert",
						"title": subject,
						"message": message,
						"indicator": "blue"
					},
					user=recipient
				)
			except Exception as e:
				frappe.log_error(
					f"Error creating system notification for {recipient}: {str(e)}",
					"Workflow Notification Error"
				)
		
	except Exception as e:
		frappe.log_error(f"Error in send_workflow_notifications: {str(e)}", "Workflow Notification Error")

