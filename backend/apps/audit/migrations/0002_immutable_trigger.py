from django.db import migrations

CREATE = """
CREATE OR REPLACE FUNCTION audit_log_block_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'audit_log rows are immutable';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER audit_log_immutable BEFORE UPDATE OR DELETE ON audit_auditlog
  FOR EACH ROW EXECUTE FUNCTION audit_log_block_mutation();
"""
DROP = "DROP TRIGGER IF EXISTS audit_log_immutable ON audit_auditlog; DROP FUNCTION IF EXISTS audit_log_block_mutation();"


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CREATE)


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(DROP)


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]
    operations = [migrations.RunPython(forwards, backwards)]
