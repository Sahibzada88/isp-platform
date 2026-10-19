import getpass
import os

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Employee, Role


class Command(BaseCommand):
    help = "Create the initial owner account (password from OWNER_PASSWORD env or prompt)."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", default="")

    def handle(self, *args, **opts):
        if Employee.objects.filter(role=Role.OWNER).exists():
            raise CommandError("An owner already exists.")
        password = os.environ.get("OWNER_PASSWORD") or getpass.getpass("Password: ")
        owner = Employee(username=opts["username"], email=opts["email"], role=Role.OWNER, employee_code="EMP-OWNER")
        owner.set_password(password)
        owner.save()
        self.stdout.write(self.style.SUCCESS(f"Owner '{owner.username}' created."))
