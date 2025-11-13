import frappe
from frappe import _
from frappe.utils import get_fullname, now_datetime
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
import json


# Store previous workflow state to detect changes
_previous_states = {}


def check_workflow_state_change(doc, method):
	"""Store the previous workflow state before update"""
	# Early exit: Only process doctypes with workflows
	# Check workflow existence first before doing ANYTHING else
	try:
		workflow = get_workflow_for_doctype(doc.doctype)
	except Exception:
		# If we can't even check for workflow, skip completely
		return
	
	if not workflow:
		# No workflow for this doctype, don't even try - exit immediately
		return
	
	try:
		
		print(f"🔍 [NOTIFICATIONS PLUGIN] Checking workflow state for {doc.doctype} {doc.name}")
		frappe.logger().info(f"[NOTIFICATIONS PLUGIN] validate hook called for {doc.doctype} {doc.name}")
		
		# This runs in validate, so we get the state from DB before changes
		if doc.name and frappe.db.exists(doc.doctype, doc.name):
			key = f"{doc.doctype}:{doc.name}"
			
			try:
				# Use the workflow's state field
				state_field = workflow.workflow_state_field
				
				# Check if the field exists in the doctype before querying
				meta = frappe.get_meta(doc.doctype)
				if not meta.has_field(state_field):
					# Field doesn't exist, try "status" as fallback
					if meta.has_field("status"):
						state_field = "status"
						print(f"⚠️ [NOTIFICATIONS PLUGIN] Workflow field '{workflow.workflow_state_field}' doesn't exist, using 'status' field instead")
					else:
						# Neither field exists, skip
						print(f"⚠️ [NOTIFICATIONS PLUGIN] Neither '{workflow.workflow_state_field}' nor 'status' field exists in {doc.doctype}, skipping")
						return
				
				previous_state = frappe.db.get_value(doc.doctype, doc.name, state_field)
				_previous_states[key] = previous_state
				print(f"📋 [NOTIFICATIONS PLUGIN] Stored previous state '{previous_state}' for {doc.doctype} {doc.name} (field: {state_field})")
			except Exception as e:
				# If there's an error (like column doesn't exist), just skip silently
				# Don't log as error since this is expected for doctypes without workflow fields
				frappe.logger().debug(f"[NOTIFICATIONS PLUGIN] Could not get previous state for {doc.doctype} {doc.name}: {str(e)}")
				return
		else:
			# New document
			key = f"{doc.doctype}:{doc.name}"
			_previous_states[key] = None
			print(f"📋 [NOTIFICATIONS PLUGIN] New document - no previous state for {doc.doctype} {doc.name}")
	except Exception as e:
		# Catch any unexpected errors and log them, but don't throw
		frappe.logger().error(f"[NOTIFICATIONS PLUGIN] Unexpected error in check_workflow_state_change for {doc.doctype}: {str(e)}")
		# Don't re-raise - just return silently
		return


def handle_workflow_transition(doc, method):
	"""Handle workflow state transitions and trigger notifications"""
	# Early exit: Only process doctypes with workflows
	# Check workflow existence first before doing ANYTHING else
	try:
		workflow = get_workflow_for_doctype(doc.doctype)
	except Exception:
		# If we can't even check for workflow, skip completely
		return
	
	if not workflow:
		# No workflow for this doctype, don't even try - exit immediately
		return
	
	try:
		
		print(f"🚀 [NOTIFICATIONS PLUGIN] on_update hook called for {doc.doctype} {doc.name}")
		frappe.logger().info(f"[NOTIFICATIONS PLUGIN] on_update hook called for {doc.doctype} {doc.name}")
		
		print(f"✅ [NOTIFICATIONS PLUGIN] Found workflow '{workflow.name}' for {doc.doctype}")
		
		# Get the workflow state field name
		state_field = workflow.workflow_state_field
		
		# Check if the field exists, fallback to "status" if not
		meta = frappe.get_meta(doc.doctype)
		if not meta.has_field(state_field):
			if meta.has_field("status"):
				state_field = "status"
				print(f"⚠️ [NOTIFICATIONS PLUGIN] Workflow field '{workflow.workflow_state_field}' doesn't exist, using 'status' field instead")
			else:
				# Neither field exists, skip
				print(f"⚠️ [NOTIFICATIONS PLUGIN] Neither '{workflow.workflow_state_field}' nor 'status' field exists in {doc.doctype}, skipping")
				return
		
		# Get current state from document using the correct field
		current_state = doc.get(state_field)
		
		# Get previous state from stored value
		key = f"{doc.doctype}:{doc.name}"
		previous_state = _previous_states.get(key)
		was_new_document = (previous_state is None)
		
		print(f"📊 [NOTIFICATIONS PLUGIN] State check - Previous: '{previous_state}', Current: '{current_state}' (field: {state_field})")
		print(f"📊 [NOTIFICATIONS PLUGIN] Was new document: {was_new_document}")
		
		# If not in cache, get from database (fallback)
		if previous_state is None and doc.name and frappe.db.exists(doc.doctype, doc.name):
			try:
				db_previous_state = frappe.db.get_value(doc.doctype, doc.name, state_field)
				print(f"📊 [NOTIFICATIONS PLUGIN] Got previous state from DB: '{db_previous_state}'")
				
				# If this was a new document (None in cache) and DB has a state,
				# check if it's different from current. If same, it means document was just created
				# with this state, so treat as initial transition
				if was_new_document and db_previous_state == current_state:
					# Document was just created with this state - treat as initial transition
					previous_state = None  # Keep as None to indicate initial state
					print(f"📊 [NOTIFICATIONS PLUGIN] New document with initial state '{current_state}' - treating as transition from None")
				else:
					previous_state = db_previous_state
			except Exception as e:
				# If we can't get the previous state from DB, just use None
				print(f"⚠️ [NOTIFICATIONS PLUGIN] Could not get previous state from DB: {str(e)}, using None")
				previous_state = None
		
		# Check if state actually changed
		if not current_state:
			print(f"⚠️ [NOTIFICATIONS PLUGIN] No current state found, skipping")
			return
		
		# For new documents, if we have a current state, treat it as a transition from None
		# For existing documents, only proceed if state actually changed
		if not was_new_document and current_state == previous_state:
			print(f"⚠️ [NOTIFICATIONS PLUGIN] State unchanged ({current_state}), skipping")
			return
		
		# Log for debugging
		print(f"🎯 [NOTIFICATIONS PLUGIN] ⭐ WORKFLOW STATE CHANGE DETECTED ⭐")
		print(f"   Document: {doc.doctype} - {doc.name}")
		print(f"   Previous State: {previous_state}")
		print(f"   Current State: {current_state}")
		print(f"   Field: {state_field}")
		frappe.logger().info(f"[NOTIFICATIONS PLUGIN] ⭐ WORKFLOW STATE CHANGE DETECTED ⭐ {doc.doctype} {doc.name} from {previous_state} to {current_state} (field: {state_field})")
		
		# Get recipients and send notifications directly via code
		print(f"👥 [NOTIFICATIONS PLUGIN] Getting recipients for {doc.doctype} {doc.name}...")
		recipients = get_notification_recipients(doc, workflow, current_state, previous_state)
		
		print(f"👥 [NOTIFICATIONS PLUGIN] Found {len(recipients)} recipients: {recipients}")
		frappe.logger().info(f"[NOTIFICATIONS PLUGIN] Recipients for {doc.doctype} {doc.name}: {recipients}")
		
		if recipients:
			print(f"📤 [NOTIFICATIONS PLUGIN] Sending notifications to {len(recipients)} recipients...")
			frappe.logger().info(f"[NOTIFICATIONS PLUGIN] Sending notifications to {len(recipients)} recipients")
			send_workflow_notifications(
				doc=doc,
				workflow=workflow,
				current_state=current_state,
				previous_state=previous_state,
				recipients=recipients
			)
			print(f"✅ [NOTIFICATIONS PLUGIN] Notifications sent successfully!")
			frappe.logger().info(f"[NOTIFICATIONS PLUGIN] Notifications sent successfully")
		else:
			print(f"⚠️ [NOTIFICATIONS PLUGIN] No recipients found for {doc.doctype} {doc.name}")
			frappe.logger().info(f"[NOTIFICATIONS PLUGIN] No recipients found for {doc.doctype} {doc.name}")
		
		# Clear stored state
		if key in _previous_states:
			del _previous_states[key]
			
	except Exception as e:
		# Log the error but don't throw - we don't want to break document saves
		frappe.log_error(f"Error in workflow notification for {doc.doctype} {doc.name}: {str(e)}", "Workflow Notification Error")
		print(f"❌ [NOTIFICATIONS PLUGIN] Error in workflow notification (non-critical): {str(e)}")
		# Don't re-raise - return silently to avoid breaking document saves
		return


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
			# Load the workflow document with all child tables (transitions, states)
			workflow_doc = frappe.get_doc("Workflow", workflow[0].name)
			# Ensure child tables are loaded
			if hasattr(workflow_doc, 'transitions'):
				print(f"🔍 [NOTIFICATIONS PLUGIN] Workflow '{workflow_doc.name}' loaded with {len(workflow_doc.transitions)} transitions")
			return workflow_doc
		return None
	except Exception as e:
		print(f"❌ [NOTIFICATIONS PLUGIN] Error loading workflow: {str(e)}")
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
	
	try:
		# 1. Add initiator (the person who created the document)
		initiator = doc.get("owner") or frappe.session.user
		if initiator:
			recipients.add(initiator)
			print(f"👤 [NOTIFICATIONS PLUGIN] Added initiator: {initiator}")
			frappe.logger().info(f"[NOTIFICATIONS PLUGIN] Added initiator: {initiator}")
		
		# 2. Get recipients from workflow transitions (allowed roles)
		print(f"🔍 [NOTIFICATIONS PLUGIN] About to call get_transition_recipients for state '{current_state}'...")
		frappe.logger().info(f"[NOTIFICATIONS PLUGIN] About to call get_transition_recipients for state '{current_state}'...")
		try:
			transition_recipients = get_transition_recipients(doc, workflow, current_state, previous_state)
			print(f"🔍 [NOTIFICATIONS PLUGIN] get_transition_recipients returned: {transition_recipients}")
			frappe.logger().info(f"[NOTIFICATIONS PLUGIN] get_transition_recipients returned: {transition_recipients}")
			recipients.update(transition_recipients)
		except Exception as e:
			print(f"❌ [NOTIFICATIONS PLUGIN] Error in get_transition_recipients: {str(e)}")
			frappe.log_error(f"Error in get_transition_recipients: {str(e)}", "Workflow Notification Error")
		
		# 3. Get recipients from custom_extra_notification_recipients_ child table
		extra_recipients = get_extra_notification_recipients(doc)
		recipients.update(extra_recipients)
		
		# Filter out None and empty values
		recipients = {r for r in recipients if r}
		
	except Exception as e:
		print(f"❌ [NOTIFICATIONS PLUGIN] Error in get_notification_recipients: {str(e)}")
		frappe.log_error(f"Error in get_notification_recipients: {str(e)}", "Workflow Notification Error")
	
	return list(recipients)


def get_transition_recipients(doc, workflow, current_state, previous_state):
	"""Get recipients from workflow transitions based on allowed roles"""
	recipients = set()
	
	print(f"🔍 [NOTIFICATIONS PLUGIN] get_transition_recipients called for state: '{current_state}' in workflow: '{workflow.name}'")
	
	try:
		# Get transitions that START FROM the current state
		# In Workflow Transition (child table of Workflow):
		# - "state" is the FROM state (current state before transition)
		# - "next_state" is the TO state (state after transition)
		# - "allowed" is the role that can make this transition
		# When we reach a state, we want to notify the roles that can make transitions FROM that state
		# (i.e., the next approvers/people who need to act on this state)
		
		# Transitions are stored as a child table in the workflow document
		# Access them via workflow.transitions (not as separate documents)
		transitions = []
		if hasattr(workflow, 'transitions') and workflow.transitions:
			print(f"🔍 [NOTIFICATIONS PLUGIN] Checking {len(workflow.transitions)} transitions in workflow...")
			for transition in workflow.transitions:
				# Check if this transition starts from the current state
				# (meaning the role in "allowed" can act on documents in the current state)
				from_state = transition.get("state")
				if from_state == current_state:
					transitions.append(transition)
					next_state = transition.get("next_state")
					allowed_role = transition.get("allowed")
					print(f"   ✅ Found transition FROM '{from_state}' -> '{next_state}' (allowed role: {allowed_role})")
		
		print(f"🔍 [NOTIFICATIONS PLUGIN] Found {len(transitions)} transitions FROM '{current_state}' (next approvers)")
		
		# Fallback: If no transitions found via child table, try querying as separate documents
		if not transitions:
			print(f"🔍 [NOTIFICATIONS PLUGIN] No transitions found in child table, trying direct query...")
			transitions = frappe.get_all(
				"Workflow Transition",
				filters={
					"parent": workflow.name,
					"state": current_state  # state is the FROM state
				},
				fields=["allowed", "role", "state", "next_state", "action"]
			)
			print(f"🔍 [NOTIFICATIONS PLUGIN] Direct query found {len(transitions)} transitions")
		
		for transition in transitions:
			# Get role from allowed field or role field
			role = transition.get("allowed") or transition.get("role")
			
			print(f"🔍 [NOTIFICATIONS PLUGIN] Processing transition: {transition.get('state')} -> {transition.get('next_state')}, allowed role: {role}")
			
			if role:
				# Get users with this role, but be careful with Employee role
				users = get_users_for_role(role, doc)
				print(f"👥 [NOTIFICATIONS PLUGIN] Found {len(users)} users for role '{role}': {users}")
				recipients.update(users)
			else:
				print(f"⚠️ [NOTIFICATIONS PLUGIN] No role found in transition")
		
	except Exception as e:
		print(f"❌ [NOTIFICATIONS PLUGIN] Error getting transition recipients: {str(e)}")
		frappe.log_error(f"Error getting transition recipients: {str(e)}", "Workflow Notification Error")
	
	print(f"👥 [NOTIFICATIONS PLUGIN] get_transition_recipients returning {len(recipients)} recipients: {recipients}")
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
		
		print(f"🔍 [NOTIFICATIONS PLUGIN] Checking for extra notification recipients in '{child_table_field}'...")
		
		if hasattr(doc, child_table_field) and doc.get(child_table_field):
			child_table_rows = doc.get(child_table_field)
			print(f"📋 [NOTIFICATIONS PLUGIN] Found {len(child_table_rows)} rows in extra notification recipients table")
			
			for row in child_table_rows:
				role = row.get("role")
				if role:
					print(f"👥 [NOTIFICATIONS PLUGIN] Processing extra recipient role: {role}")
					users = get_users_for_role(role, doc)
					print(f"👥 [NOTIFICATIONS PLUGIN] Found {len(users)} users for extra role '{role}': {users}")
					recipients.update(users)
				else:
					print(f"⚠️ [NOTIFICATIONS PLUGIN] Row in extra notification recipients has no role field")
		else:
			print(f"ℹ️ [NOTIFICATIONS PLUGIN] No extra notification recipients table found or empty")
		
		print(f"✅ [NOTIFICATIONS PLUGIN] Extra notification recipients: {len(recipients)} total users")
		
	except Exception as e:
		print(f"❌ [NOTIFICATIONS PLUGIN] Error getting extra notification recipients: {str(e)}")
		frappe.log_error(f"Error getting extra notification recipients: {str(e)}", "Workflow Notification Error")
	
	return recipients


def send_workflow_notifications(doc, workflow, current_state, previous_state, recipients):
	"""Send email notifications to all recipients"""
	if not recipients:
		frappe.logger().info("No recipients to send notifications to")
		return
	
	try:
		frappe.logger().info(f"Preparing notifications for {doc.doctype} {doc.name}")
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
		
		# Filter recipients - get their emails
		# Note: We don't skip the current user if they're the initiator or in the workflow recipients
		# because they should be notified of workflow progression
		initiator = doc.get("owner") or frappe.session.user
		notification_user_emails = []
		for recipient in recipients:
			# Only skip if recipient is current user AND they're not the initiator
			# (initiator should always be notified of workflow progression)
			if recipient == frappe.session.user:
				if recipient == initiator:
					print(f"✅ [NOTIFICATIONS PLUGIN] Including current user (initiator): {recipient}")
				else:
					print(f"⏭️ [NOTIFICATIONS PLUGIN] Skipping current user (not initiator): {recipient}")
					continue
			
			# Check if recipient is already an email or a user ID
			user_email = None
			if "@" in str(recipient):
				# Looks like an email, verify it's a valid user email
				user_email = frappe.db.get_value("User", {"email": recipient, "enabled": 1}, "email")
				if user_email:
					print(f"📧 [NOTIFICATIONS PLUGIN] Recipient is email: {user_email}")
				else:
					print(f"⚠️ [NOTIFICATIONS PLUGIN] Email {recipient} not found for enabled user, skipping")
			else:
				# Assume it's a user ID, get the email
				user_email = frappe.db.get_value("User", {"name": recipient, "enabled": 1}, "email")
				if user_email:
					print(f"👤 [NOTIFICATIONS PLUGIN] User ID {recipient} -> Email: {user_email}")
				else:
					print(f"⚠️ [NOTIFICATIONS PLUGIN] User ID {recipient} not found or disabled, skipping")
			
			if user_email:
				notification_user_emails.append(user_email)
		
		print(f"📬 [NOTIFICATIONS PLUGIN] Final notification emails: {notification_user_emails}")
		
		if notification_user_emails:
			try:
				# Create system notification using Frappe's notification system
				# This will show as a popup in the notification center
				# The link field makes the notification clickable to open the document
				notification_doc = {
					"type": "Alert",
					"document_type": doc.doctype,
					"document_name": doc.name,
					"subject": subject,
					"from_user": frappe.session.user,
					"email_content": message,
					"link": doc_link,  # This makes the notification clickable
				}
				print(f"📤 [NOTIFICATIONS PLUGIN] Calling enqueue_create_notification with:")
				print(f"   Users: {notification_user_emails}")
				print(f"   Doc: {notification_doc}")
				enqueue_create_notification(notification_user_emails, notification_doc)
				print(f"✅ [NOTIFICATIONS PLUGIN] enqueue_create_notification called successfully")
				frappe.logger().info(f"[NOTIFICATIONS PLUGIN] System notifications created for {len(notification_user_emails)} users")
				
				# Also try push notifications if enabled
				try:
					from frappe.push_notification import PushNotification
					push_notification = PushNotification("notifications_plugin")
					if push_notification.is_enabled():
						# Get document link
						doc_link = frappe.utils.get_url_to_form(doc.doctype, doc.name)
						
						# Send push notification to each user
						for user_email in notification_user_emails:
							# Get user ID from email
							user_id = frappe.db.get_value("User", {"email": user_email}, "name")
							if user_id:
								push_notification.send_notification_to_user(
									user_id=user_id,
									title=subject,
									body=_("Workflow transition: {0} moved from {1} to {2}").format(
										doc.name, previous_state or _("Initial"), current_state
									),
									link=doc_link
								)
				except ImportError:
					# Push notifications not available, skip
					pass
				except Exception as e:
					# Don't fail if push notifications fail
					frappe.logger().error(f"Push notification error (non-critical): {str(e)}")
			except Exception as e:
				frappe.log_error(
					f"Error creating system notifications: {str(e)}",
					"Workflow Notification Error"
				)
		
	except Exception as e:
		frappe.log_error(f"Error in send_workflow_notifications: {str(e)}", "Workflow Notification Error")

