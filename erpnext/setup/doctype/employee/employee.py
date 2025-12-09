# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt
import frappe
from frappe import _, scrub, throw
from frappe.model.naming import set_name_by_naming_series
from frappe.permissions import (
	add_user_permission,
	get_doc_permissions,
	has_permission,
	remove_user_permission,
)
from frappe.utils import cstr, getdate, today, validate_email_address
from frappe.utils.deprecations import deprecated
from frappe.utils.nestedset import NestedSet
from erpnext import get_default_company
from erpnext.utilities.transaction_base import delete_events


class EmployeeUserDisabledError(frappe.ValidationError):
	pass


class InactiveEmployeeStatusError(frappe.ValidationError):
	pass


class Employee(NestedSet):
	nsm_parent_field = "reports_to"

	def autoname(self):
		set_name_by_naming_series(self)
		self.employee = self.name

	def validate(self):
		from erpnext.controllers.status_updater import validate_status

		validate_status(self.status, ["Active", "Inactive", "Suspended", "Left"])

		self.employee = self.name
		self.set_employee_name()
		self.validate_date()
		self.validate_email()
		self.validate_status()
		self.validate_reports_to()
		self.validate_preferred_email()

		if self.user_id:
			self.validate_user_details()
		else:
			existing_user_id = frappe.db.get_value("Employee", self.name, "user_id")
			if existing_user_id:
				user = frappe.get_doc("User", existing_user_id)
				validate_employee_role(user, ignore_emp_check=True)
				user.save(ignore_permissions=True)
				remove_user_permission("Employee", self.name, existing_user_id)

	def after_rename(self, old, new, merge):
		self.db_set("employee", new)

	def set_employee_name(self):
		self.employee_name = " ".join(
			filter(lambda x: x, [self.first_name, self.middle_name, self.last_name])
		)

	def validate_user_details(self):
		if self.user_id:
			data = frappe.db.get_value("User", self.user_id, ["enabled"], as_dict=1)

			if not data:
				self.user_id = None
				return

			self.validate_for_enabled_user_id(data.get("enabled", 0))
			self.validate_duplicate_user_id()

	def update_nsm_model(self):
		frappe.utils.nestedset.update_nsm(self)

	def on_update(self):
		self.update_nsm_model()
		frappe.clear_cache()
		if self.user_id:
			self.update_user()
			self.update_user_permissions()
		self.reset_employee_emails_cache()

	def update_user_permissions(self):
		if not self.has_value_changed("user_id") and not self.has_value_changed("create_user_permission"):
			return

		if not has_permission("User Permission", ptype="write", raise_exception=False):
			return

		if self.create_user_permission:
			approved_doctypes = frappe.db.sql(f"""
									SELECT 
										ref_doctype FROM `tabDoctype List`
									WHERE 
										parent = "{self.company}"
									GROUP BY ref_doctype
									""", as_dict=1)
		
			formatted_doctypes = [x.get('ref_doctype') for x in approved_doctypes]
			doctypes_tuple = "(" + ", ".join([f'"{doctype}"' for doctype in formatted_doctypes]) + ")"
			lock_doctypes = frappe.db.sql(f"""
								SELECT
									parent as doctype
								FROM
									`tabDocField`
								WHERE
									fieldtype = "Link"
									AND OPTIONS = "Employee"
									AND parent not in {doctypes_tuple}
								GROUP BY
									parent
									""", as_dict=1)

			for item in lock_doctypes:
				dt = item.get('doctype')
				employee_user_permission_exists = frappe.db.exists(
					"User Permission", {"allow": "Employee", "for_value": self.name, "user": self.user_id, 'applicable_for': dt}
				)
				
				if employee_user_permission_exists:
					continue
			
				add_user_permission("Employee", self.name, self.user_id, applicable_for=dt)
		
		# Setting user permissions for additional leave approvers and adding new approver to existing docs.
		if self.approvers:
			for approver in self.approvers:
				if approver.get("leave_approver"):
					if not frappe.db.exists("Employee", {"user_id": approver.leave_approver}):
						frappe.msgprint(f"Unable to find a valid employee for user {approver.leave_approver}, unable to add this additional approver user permission.")
						continue

					if not frappe.db.exists("User Permission", {"allow": "Employee", "for_value": self.name, "user": approver.leave_approver, "applicable_for": "Leave Application"}):
						add_user_permission("Employee", self.name, approver.leave_approver, applicable_for="Leave Application", hide_descendants=1)

					leave_applications = frappe.get_all("Leave Application", filters={"employee": self.name, "status": "Open"}, fields=["name"])
					for leave_application in leave_applications:
						leave_doc = frappe.get_doc("Leave Application", leave_application.name)
						if approver.leave_approver not in [d.leave_approver for d in leave_doc.additional_leave_approvers]:
							leave_doc.append("additional_leave_approvers", {"leave_approver": approver.leave_approver, "notification_level": approver.notification_level})
							leave_doc.save(ignore_permissions=True)
							frappe.db.commit()

	def update_user(self):
		# add employee role if missing
		user = frappe.get_doc("User", self.user_id)
		user.flags.ignore_permissions = True

		if "Employee" not in user.get("roles"):
			user.append_roles("Employee")

		# copy details like Fullname, DOB and Image to User
		if self.employee_name and not (user.first_name and user.last_name):
			employee_name = self.employee_name.split(" ")
			if len(employee_name) >= 3:
				user.last_name = " ".join(employee_name[2:])
				user.middle_name = employee_name[1]
			elif len(employee_name) == 2:
				user.last_name = employee_name[1]

			user.first_name = employee_name[0]

		if self.date_of_birth:
			user.birth_date = self.date_of_birth

		if self.gender:
			user.gender = self.gender

		if self.image:
			if not user.user_image:
				user.user_image = self.image
				try:
					frappe.get_doc(
						{
							"doctype": "File",
							"file_url": self.image,
							"attached_to_doctype": "User",
							"attached_to_name": self.user_id,
						}
					).insert(ignore_if_duplicate=True)
				except frappe.DuplicateEntryError:
					# already exists
					pass

		user.save()

	def validate_date(self):
		if self.date_of_birth and getdate(self.date_of_birth) > getdate(today()):
			throw(_("Date of Birth cannot be greater than today."))

		self.validate_from_to_dates("date_of_birth", "date_of_joining")
		self.validate_from_to_dates("date_of_joining", "date_of_retirement")
		self.validate_from_to_dates("date_of_joining", "relieving_date")
		self.validate_from_to_dates("date_of_joining", "contract_end_date")

	def validate_email(self):
		if self.company_email:
			validate_email_address(self.company_email, True)
		if self.personal_email:
			validate_email_address(self.personal_email, True)

	def set_preferred_email(self):
		preferred_email_field = frappe.scrub(self.prefered_contact_email)
		if preferred_email_field:
			preferred_email = self.get(preferred_email_field)
			self.prefered_email = preferred_email

	def validate_status(self):
		if self.status == "Left":
			reports_to = frappe.db.get_all(
				"Employee",
				filters={"reports_to": self.name, "status": "Active"},
				fields=["name", "employee_name"],
			)
			if reports_to:
				link_to_employees = [
					frappe.utils.get_link_to_form("Employee", employee.name, label=employee.employee_name)
					for employee in reports_to
				]
				message = _("The following employees are currently still reporting to {0}:").format(
					frappe.bold(self.employee_name)
				)
				message += "<br><br><ul><li>" + "</li><li>".join(link_to_employees)
				message += "</li></ul><br>"
				message += _("Please make sure the employees above report to another Active employee.")
				throw(message, InactiveEmployeeStatusError, _("Cannot Relieve Employee"))
			if not self.relieving_date:
				throw(_("Please enter relieving date."))

	def validate_for_enabled_user_id(self, enabled):
		if not self.status == "Active":
			return

		if enabled is None:
			frappe.throw(_("User {0} does not exist").format(self.user_id))
		if enabled == 0:
			frappe.throw(_("User {0} is disabled").format(self.user_id), EmployeeUserDisabledError)

	def validate_duplicate_user_id(self):
		Employee = frappe.qb.DocType("Employee")
		employee = (
			frappe.qb.from_(Employee)
			.select(Employee.name)
			.where(
				(Employee.user_id == self.user_id)
				& (Employee.status == "Active")
				& (Employee.name != self.name)
			)
		).run()
		if employee:
			throw(
				_("User {0} is already assigned to Employee {1}").format(self.user_id, employee[0][0]),
				frappe.DuplicateEntryError,
			)

	def validate_reports_to(self):
		if self.reports_to == self.name:
			throw(_("Employee cannot report to himself."))

	def on_trash(self):
		self.update_nsm_model()
		delete_events(self.doctype, self.name)

	def validate_preferred_email(self):
		if self.prefered_contact_email and not self.get(scrub(self.prefered_contact_email)):
			frappe.msgprint(_("Please enter {0}").format(self.prefered_contact_email))

	def reset_employee_emails_cache(self):
		prev_doc = self.get_doc_before_save() or {}
		cell_number = cstr(self.get("cell_number"))
		prev_number = cstr(prev_doc.get("cell_number"))
		if cell_number != prev_number or self.get("user_id") != prev_doc.get("user_id"):
			frappe.cache().hdel("employees_with_number", cell_number)
			frappe.cache().hdel("employees_with_number", prev_number)

	@frappe.whitelist()
	def populate_work_schedule(self):
		self.work_hours = []
		
		days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
		
		for week in range(2):
			for day in days:
				row = self.append('work_hours', {})
				row.day = day

				if day in ['Saturday', 'Sunday']:
					row.hours = 0
					row.minutes = '0'
				else:
					row.hours = 8
					row.minutes = '0'

		self.total_work_hours = 80
		self.total_work_minutes = 0


def validate_employee_role(doc, method=None, ignore_emp_check=False):
	# called via User hook
	if not ignore_emp_check:
		if frappe.db.get_value("Employee", {"user_id": doc.name}):
			return

	user_roles = [d.role for d in doc.get("roles")]
	if "Employee" in user_roles:
		frappe.msgprint(_("User {0}: Removed Employee role as there is no mapped employee.").format(doc.name))
		doc.get("roles").remove(doc.get("roles", {"role": "Employee"})[0])

	if "Employee Self Service" in user_roles:
		frappe.msgprint(
			_("User {0}: Removed Employee Self Service role as there is no mapped employee.").format(doc.name)
		)
		doc.get("roles").remove(doc.get("roles", {"role": "Employee Self Service"})[0])


@deprecated
def update_user_permissions(doc, method):
	# formerly called via User hook
	if "Employee" in [d.role for d in doc.get("roles")]:
		if not has_permission("User Permission", ptype="write", raise_exception=False):
			return
		employee = frappe.get_doc("Employee", {"user_id": doc.name})
		employee.update_user_permissions()


def get_employee_email(employee_doc):
	return (
		employee_doc.get("user_id") or employee_doc.get("personal_email") or employee_doc.get("company_email")
	)


def get_holiday_list_for_employee(employee, raise_exception=True):
	if employee:
		holiday_list, company = frappe.get_cached_value("Employee", employee, ["holiday_list", "company"])
	else:
		holiday_list = ""
		company = frappe.db.get_single_value("Global Defaults", "default_company")

	if not holiday_list:
		holiday_list = frappe.get_cached_value("Company", company, "default_holiday_list")

	if not holiday_list and raise_exception:
		frappe.throw(
			_("Please set a default Holiday List for Employee {0} or Company {1}").format(employee, company)
		)

	return holiday_list


def is_holiday(employee, date=None, raise_exception=True, only_non_weekly=False, with_description=False):
	"""
	Returns True if given Employee has an holiday on the given date
	        :param employee: Employee `name`
	        :param date: Date to check. Will check for today if None
	        :param raise_exception: Raise an exception if no holiday list found, default is True
	        :param only_non_weekly: Check only non-weekly holidays, default is False
	"""

	holiday_list = get_holiday_list_for_employee(employee, raise_exception)
	if not date:
		date = today()

	if not holiday_list:
		return False

	filters = {"parent": holiday_list, "holiday_date": date}
	if only_non_weekly:
		filters["weekly_off"] = False

	holidays = frappe.get_all("Holiday", fields=["description"], filters=filters, pluck="description")

	if with_description:
		return len(holidays) > 0, holidays

	return len(holidays) > 0


@frappe.whitelist()
def deactivate_sales_person(status=None, employee=None):
	if status == "Left":
		sales_person = frappe.db.get_value("Sales Person", {"Employee": employee})
		if sales_person:
			frappe.db.set_value("Sales Person", sales_person, "enabled", 0)


@frappe.whitelist()
def create_user(employee, user=None, email=None):
	emp = frappe.get_doc("Employee", employee)

	employee_name = emp.employee_name.split(" ")
	middle_name = last_name = ""

	if len(employee_name) >= 3:
		last_name = " ".join(employee_name[2:])
		middle_name = employee_name[1]
	elif len(employee_name) == 2:
		last_name = employee_name[1]

	first_name = employee_name[0]

	if email:
		emp.prefered_email = email

	user = frappe.new_doc("User")
	user.update(
		{
			"name": emp.employee_name,
			"email": emp.prefered_email,
			"enabled": 1,
			"first_name": first_name,
			"middle_name": middle_name,
			"last_name": last_name,
			"gender": emp.gender,
			"birth_date": emp.date_of_birth,
			"phone": emp.cell_number,
			"bio": emp.bio,
		}
	)
	user.insert()
	emp.user_id = user.name
	emp.save()
	return user.name


def get_all_employee_emails(company):
	"""Returns list of employee emails either based on user_id or company_email"""
	employee_list = frappe.get_all(
		"Employee", fields=["name", "employee_name"], filters={"status": "Active", "company": company}
	)
	employee_emails = []
	for employee in employee_list:
		if not employee:
			continue
		user, company_email, personal_email = frappe.db.get_value(
			"Employee", employee, ["user_id", "company_email", "personal_email"]
		)
		email = user or company_email or personal_email
		if email:
			employee_emails.append(email)
	return employee_emails


def get_employee_emails(employee_list):
	"""Returns list of employee emails either based on user_id or company_email"""
	employee_emails = []
	for employee in employee_list:
		if not employee:
			continue
		user, company_email, personal_email = frappe.db.get_value(
			"Employee", employee, ["user_id", "company_email", "personal_email"]
		)
		email = user or company_email or personal_email
		if email:
			employee_emails.append(email)
	return employee_emails


@frappe.whitelist()
def get_children(doctype, parent=None, company=None, is_root=False, is_tree=False):
	filters = [["status", "=", "Active"]]
	if company and company != "All Companies":
		filters.append(["company", "=", company])

	fields = ["name as value", "employee_name as title"]

	if is_root:
		parent = ""
	if parent and company and parent != company:
		filters.append(["reports_to", "=", parent])
	else:
		filters.append(["reports_to", "=", ""])

	employees = frappe.get_list(doctype, fields=fields, filters=filters, order_by="name")

	for employee in employees:
		is_expandable = frappe.get_all(doctype, filters=[["reports_to", "=", employee.get("value")]])
		employee.expandable = 1 if is_expandable else 0

	return employees


def on_doctype_update():
	frappe.db.add_index("Employee", ["lft", "rgt"])


def has_user_permission_for_employee(user_name, employee_name):
	return frappe.db.exists(
		{
			"doctype": "User Permission",
			"user": user_name,
			"allow": "Employee",
			"for_value": employee_name,
		}
	)


def has_upload_permission(doc, ptype="read", user=None):
	if not user:
		user = frappe.session.user
	if get_doc_permissions(doc, user=user, ptype=ptype).get(ptype):
		return True
	return doc.user_id == user


# from erpnext.setup.doctype.employee.employee import load_perm
def load_perm():
	employees = frappe.get_all('Employee', 
		filters={
			'user_id': ['!=', ''],
			'create_user_permission': 1
		},
		fields=['name', 'user_id'])
	
	approved_doctypes = frappe.db.sql(f"""
							SELECT 
								ref_doctype FROM `tabDoctype List`
							WHERE 
								parent = "{get_default_company()}"
							GROUP BY ref_doctype
							""", as_dict=1)

	formatted_doctypes = [x.get('ref_doctype') for x in approved_doctypes]
	doctypes_tuple = "(" + ", ".join([f'"{doctype}"' for doctype in formatted_doctypes]) + ")"
	lock_doctypes = frappe.db.sql(f"""
						SELECT
							parent as doctype
						FROM
							`tabDocField`
						WHERE
							fieldtype = "Link"
							AND OPTIONS = "Employee"
							AND parent not in {doctypes_tuple}
						GROUP BY
							parent
							""", as_dict=1)
	for employee in employees:
		for item in lock_doctypes:
			dt = item.get('doctype')
			employee_user_permission_exists = frappe.db.exists(
				"User Permission", {"allow": "Employee", "for_value": employee.name, "user": employee.user_id, 'applicable_for': dt}
			)
   
			if employee_user_permission_exists:
				continue

			frappe.get_doc(
				dict(
					doctype="User Permission",
					user=employee.user_id,
					allow="Employee",
					for_value=employee.name,
					is_default=0,
					applicable_for=dt,
					apply_to_all_doctypes=0,
					hide_descendants=0,
				)
			).insert(ignore_permissions=0)
			print(f"Loaded Perms for {employee.user_id} for dt: {dt}")
		frappe.db.commit()