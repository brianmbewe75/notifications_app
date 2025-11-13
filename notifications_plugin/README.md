# Notifications Plugin

A Frappe app that sends email and system notifications when workflow states transition.

## Features

- Automatically detects workflow state changes (from `workflow_state` or custom workflow state field)
- Sends notifications to:
  - The document initiator (owner)
  - Users with roles specified in workflow transition's "allowed" field
  - Users with roles specified in `custom_extra_notification_recipients_` child table
- Special handling for "Employee" role to avoid sending to everyone
- Sends both email and system notifications
- Uses Frappe's built-in Notification doctype system for easy maintenance

## Installation

1. Install the app:
```bash
bench get-app notifications_plugin https://github.com/brianmbewe75/notifications_app
bench install-app notifications_plugin
bench restart
```

## Usage

### Option 1: Using Frappe Notification Doctype (Recommended)

1. Go to **Notification** doctype
2. Create a new Notification record:
   - **Document Type**: Your doctype (e.g., "Loan Application")
   - **Event**: Select "Method"
   - **Method**: Enter `workflow_state_changed`
   - **Channel**: Select "Email" and/or "System Notification"
   - **Subject**: `Workflow Transition: {{ doc.doctype }} - {{ doc.name }}`
   - **Message**: Your notification message template
   - **Recipients**: Configure based on your needs (can use Jinja templates)
   - **Condition**: Optional - add conditions if needed
3. Save and enable the notification

The app will automatically trigger this notification when workflow state changes.

### Option 2: Automatic Notifications (Works out of the box)

The app also sends notifications automatically based on:
- Document owner (initiator)
- Roles in workflow transition "allowed" field
- Roles in `custom_extra_notification_recipients_` child table

No additional setup needed - it works automatically for all workflows!

## Configuration

### Custom Extra Notification Recipients

To add extra recipients, add a child table field to your doctype:

1. Go to your DocType Customize form
2. Add a child table field named: `custom_extra_notification_recipients_`
3. Link it to a child DocType with a "role" field
4. Add roles to this table - users with these roles will always receive notifications

## How It Works

1. When a document is saved, the `validate` hook stores the previous workflow state
2. When the document is updated, the `on_update` hook detects if the workflow state changed
3. If changed, it:
   - Triggers Frappe Notification records with method "workflow_state_changed"
   - Also sends direct notifications to initiator, transition roles, and extra recipients
4. Notifications are sent via email and system notifications

## Notes

- The "Employee" role is handled specially to avoid sending to all employees
- Only sends to specific employees related to the document
- Works with any workflow state field name (not just "workflow_state")
- Automatically detects the correct field from workflow configuration

## Troubleshooting

Check the logs to see what's happening:
```bash
tail -f logs/web.log | grep "Workflow state change"
```

Look for messages like:
- "Workflow state change detected: ..."
- "Triggered notifications for ..."
- "Recipients for ..."

