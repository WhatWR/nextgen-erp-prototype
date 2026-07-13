app_name = "nextgen_erp"
app_title = "NextGen ERP"
app_publisher = "NextGen"
app_description = "NextGen AI order intake and order-to-cash orchestration for ERPNext"
app_email = "admin@nextgen.local"
app_license = "mit"

required_apps = ["erpnext"]
before_install = "nextgen_erp.install.before_install"
after_install = "nextgen_erp.install.after_install"
after_migrate = ["nextgen_erp.install.after_migrate"]

# Desk navbar / app-switcher logo for the AI order agent
app_logo_url = "/assets/nextgen_erp/images/nextgen-icon.svg"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "nextgen_erp",
# 		"logo": "/assets/nextgen_erp/logo.png",
# 		"title": "NextGen ERP",
# 		"route": "/nextgen_erp",
# 		"has_permission": "nextgen_erp.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/nextgen_erp/css/nextgen_erp.css"
# app_include_js = "/assets/nextgen_erp/js/nextgen_erp.js"

# include js, css files in header of web template
# web_include_css = "/assets/nextgen_erp/css/nextgen_erp.css"
# web_include_js = "/assets/nextgen_erp/js/nextgen_erp.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "nextgen_erp/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "nextgen_erp/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "nextgen_erp.utils.jinja_methods",
# 	"filters": "nextgen_erp.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "nextgen_erp.install.before_install"
# after_install = "nextgen_erp.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "nextgen_erp.uninstall.before_uninstall"
# after_uninstall = "nextgen_erp.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "nextgen_erp.utils.before_app_install"
# after_app_install = "nextgen_erp.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "nextgen_erp.utils.before_app_uninstall"
# after_app_uninstall = "nextgen_erp.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "nextgen_erp.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "nextgen_erp.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"nextgen_erp.tasks.all"
# 	],
# 	"daily": [
# 		"nextgen_erp.tasks.daily"
# 	],
# 	"hourly": [
# 		"nextgen_erp.tasks.hourly"
# 	],
# 	"weekly": [
# 		"nextgen_erp.tasks.weekly"
# 	],
# 	"monthly": [
# 		"nextgen_erp.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "nextgen_erp.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "nextgen_erp.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "nextgen_erp.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "nextgen_erp.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["nextgen_erp.utils.before_request"]
# after_request = ["nextgen_erp.utils.after_request"]

# Job Events
# ----------
# before_job = ["nextgen_erp.utils.before_job"]
# after_job = ["nextgen_erp.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"nextgen_erp.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []



# ---------------------------------------------------------------------------
# Phase 2/3 follow-up (design stub): notify the external order-intake service
# when ERPNext advances an order, so it can send the LINE reply.
#
# Prefer ERPNext "Webhook" records (no code) pointing at the service's
# /webhooks/erpnext endpoint. If code-driven events are needed instead:
#
# doc_events = {
#     "Delivery Note":  {"on_submit": "nextgen_erp.notify.on_delivery"},
#     "Sales Invoice":  {"on_submit": "nextgen_erp.notify.on_invoice"},
#     "Payment Entry":  {"on_submit": "nextgen_erp.notify.on_payment"},
# }
# Each handler looks up the linked AI Order Intake and POSTs status to
# NextGen Automation Settings.external_service_url.


# Register the AI order agent on the apps screen / switcher with its own logo
add_to_apps_screen = [
	{
		"name": "nextgen_erp",
		"logo": "/assets/nextgen_erp/images/nextgen-icon.svg",
		"title": "NextGen Order AI",
		"route": "/app/order-agent",
	}
]
