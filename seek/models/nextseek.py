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
        name = self.user.first_name + ' ' + self.user.last_name
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
