"""Tables NExtSEEK itself defines and writes.

Most set ``_DATABASE = NEXTSEEK_DATABASE``. ``User_profile``, ``Sample_tree`` and
``Session_state`` set no ``_DATABASE`` at all and so fall to Django's default
alias -- pre-existing and deliberate; changing it would move data between schemas.
"""

from django.db import models
from django.conf import settings

NEXTSEEK_DATABASE = settings.NEXTSEEK_DATABASE

PROJECT_CHOICES = (
    ("Undefined", "Undefined"),
    ("IMPAcTb", "IMPAcTb"),
    ("MIT_SRP", "MIT_SRP"),
    ("MetNet", "MetNet"),
    ("MIT-Koch", "MIT-Koch"),
    ("Training/Test", "Training/Test")
)


class User_profile(models.Model):
    user = models.OneToOneField("auth.User", on_delete=models.CASCADE)
    project = models.CharField(max_length=255, choices=PROJECT_CHOICES, editable = True)
    laboratory = models.TextField()
    
    def loginname(self):
        return self.user.username
    
    def fullname(self):
        name = self.user.first_name + ' ' + self.user.last_name  # noqa: F841 (LATENT_BUGS #37)
        return
    def __unicode__(self):
        return self.fullname()

class Sample_tree(models.Model):
    sample_id = models.IntegerField()
    uuid = models.CharField(max_length=255, default=None)
    parents = models.TextField(default=None)
    children = models.TextField(default=None)
    full = models.TextField(default=None)
    updated = models.DateTimeField(default=None)
    
    def getUUID(self):
        return self.uuid 

    def __unicode__(self):
        return self.sample_id
    
    class Meta:
        db_table = "seek_sample_tree"

class Session_state(models.Model):
    session_id = models.CharField(max_length=255, null=False)
    key = models.CharField(max_length=255, null=False)
    value = models.TextField()
    
    class Meta:
        db_table = "session_state"
        unique_together = ("session_id", "key")

class Internal_assays(models.Model):
    _DATABASE = NEXTSEEK_DATABASE
    
    internal_assay_title = models.TextField(default=None)

    class Meta:
        db_table = "internal_assays"

class Assays_internal_assays(models.Model):
    _DATABASE = NEXTSEEK_DATABASE

    internal_assay_id = models.IntegerField(default=None, null=True)
    assay_id = models.IntegerField(default=None, null=True)

    class Meta:
        db_table = "assays_internal_assays"

class Clades(models.Model):
    _DATABASE = NEXTSEEK_DATABASE

    title = models.TextField(default=None)
    color = models.TextField(default=None)
    order = models.IntegerField()
    
    def __unicode__(self):
        return self.color

    class Meta:
        db_table = "clades"

class Sample_types_clades(models.Model):
    _DATABASE = NEXTSEEK_DATABASE

    #clade = models.ForeignKey(Clades, null=True, blank=True, on_delete=models.PROTECT)
    #sample_type = models.ForeignKey(Sample_types, on_delete=models.PROTECT)

    clade_id = models.IntegerField(default=None, null=True)
    sample_type_id = models.IntegerField(default=None, null=True)

    def __unicode__(self):
        return self.clade_id + ' ' + self.sample_type_id

    class Meta:
        db_table = "sample_types_clades"

class Sample_types_context(models.Model):
    _DATABASE = NEXTSEEK_DATABASE

    sampletype_id = models.IntegerField(default=None, null=True)
    sample_type = models.CharField(max_length=32, default=None, null=True)
    name = models.CharField(max_length=255, default=None, null=True)
    description = models.TextField(default=None, null=True)
    required_metadata = models.TextField(default=None, null=True)
    standard_metadata = models.TextField(default=None, null=True)
    possible_metadata_fields = models.TextField(default=None, null=True)
    clade = models.CharField(max_length=64, default=None, null=True)
    sampletype_file_link = models.CharField(max_length=255, default=None, null=True)
    associated_assay_parents = models.TextField(default=None, null=True)
    associated_assay_children = models.TextField(default=None, null=True)
    parent_sampletypes = models.TextField(default=None, null=True)
    child_sampletypes = models.TextField(default=None, null=True)
    tags = models.TextField(db_column="Tags", default=None, null=True)

    def __unicode__(self):
        return self.sample_type

    class Meta:
        db_table = "sample_types_context"


class Sample_attributes_unique(models.Model):
    """Plain-English meaning per metadata field, for the download README.

    Joined on the `field_name` string, never on an id: ids do not agree across
    instances. `sample_type` is the scope — '' is the definition used for every
    tab, a sample type code overrides it for that tab only.
    """
    _DATABASE = NEXTSEEK_DATABASE

    field_name = models.CharField(max_length=255)
    sample_type = models.CharField(max_length=32, default="")
    meaning = models.TextField(default=None, null=True)

    def __str__(self):
        return self.field_name

    class Meta:
        db_table = "sample_attributes_unique"
        unique_together = ("field_name", "sample_type")
        # The table is created out-of-band in SQL (startup/seed/sql/
        # sample_attributes_unique.sql, applied by startup's schema fixups); this
        # model only maps it. Left managed, the next unrelated `makemigrations`
        # in this app would propose creating the table, and CustomRouter.
        # allow_migrate returns None for non-`default` app labels, so applying
        # that migration would create it on both the default and seek aliases.
        managed = False


class Sample_type_requirements(models.Model):
    """What the Download Templates picker adds when a sample type is ticked.

    Filled by `manage.py derive_sample_type_requirements` from Neo4j's
    DERIVED_FROM edges. One row per (kind, trigger_code); `add_codes` is a JSON
    array. The columns are named for the direction the user experiences,
    because the two kinds run opposite ways:

    `requires`  -- trigger_code is a child type, add_codes the parents it cannot
                   be uploaded without. One is a hard requirement, two or three
                   are alternatives of which the upload needs one.
    `companion` -- trigger_code is a parent type, add_codes the single child
                   that dominates what it produces. Not required; predicted.

    Unmanaged for the same reason as Sample_attributes_unique above: the table
    is created out-of-band in SQL (startup/seed/sql/sample_type_requirements.sql,
    applied by startup's schema fixups) and a managed model would have an
    unrelated makemigrations propose creating it on both aliases.
    """
    _DATABASE = NEXTSEEK_DATABASE

    KIND_REQUIRES = "requires"
    KIND_COMPANION = "companion"

    kind = models.CharField(max_length=16, default=KIND_REQUIRES)
    trigger_code = models.CharField(max_length=32)
    add_codes = models.TextField()
    coverage = models.DecimalField(max_digits=4, decimal_places=3)
    support = models.IntegerField()
    assay_titles = models.TextField(default=None, null=True)
    source = models.CharField(max_length=16, default="graph")
    computed_at = models.DateTimeField()

    def __str__(self):
        return f"{self.kind}:{self.trigger_code}"

    class Meta:
        db_table = "sample_type_requirements"
        managed = False
        unique_together = ("kind", "trigger_code")
