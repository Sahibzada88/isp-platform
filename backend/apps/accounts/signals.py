from django.dispatch import Signal

# Sent after an employee is disabled; complaint/task modules reassign pending work here.
employee_disabled = Signal()
