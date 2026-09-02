from __future__ import unicode_literals

from django.db import models
from django.contrib.auth.models import User

from mezzanine.pages.models import Page
from dmac.conversion import dateconversion, toDate
from datetime import date

from django.conf import settings
SEEK_DATABASE = settings.SEEK_DATABASE
NEXTSEEK_DATABASE = settings.NEXTSEEK_DATABASE

class Users(models.Model):
    _DATABASE = SEEK_DATABASE
    
    login = models.CharField(max_length=255, default=None)
    crypted_password = models.CharField(max_length=255, default=None)
    salt = models.CharField(max_length=255, default=None)
    created_at = models.DateTimeField(null=False)
    updated_at = models.DateTimeField(null=False)
    remember_token = models.CharField(max_length=255, default=None)
    remember_token_expires_at = models.DateTimeField(null=False)
    activation_code = models.CharField(max_length=255, default=None)
    activated_at = models.DateTimeField(null=False)
    person_id = models.IntegerField(default=None)
    reset_password_code = models.CharField(max_length=255, default=None)
    reset_password_code_until = models.DateTimeField(null=False)
    posts_count = models.IntegerField(default=None)
    last_seen_at = models.DateTimeField(null=False)
    uuid = models.CharField(max_length=255, default=None)
    
    def __unicode__(self):
        return self.login
    
    class Meta:
        db_table = "users"

class People(models.Model):
    _DATABASE = SEEK_DATABASE
    
    created_at = models.DateTimeField(null=False)
    updated_at = models.DateTimeField(null=False)
    first_name = models.CharField(max_length=255, default=None)
    last_name = models.CharField(max_length=255, default=None)
    email = models.CharField(max_length=255, default=None)
    phone = models.CharField(max_length=255, default=None)
    skype_name = models.CharField(max_length=255, default=None)
    web_page = models.CharField(max_length=255, default=None)
    description = models.TextField(default=None)
    avatar_id = models.IntegerField(default=None)
    status_id = models.IntegerField(default=None)
    first_letter = models.CharField(max_length=1, default=None)
    uuid = models.CharField(max_length=255, default=None)
    roles_mask = models.IntegerField(default=None)
    orcid = models.CharField(max_length=255, default=None)
    
    def __unicode__(self):
        return self.email
    
    class Meta:
        db_table = "people"


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

class Assays(models.Model):
    _DATABASE = SEEK_DATABASE

    title = models.TextField(default=None)
    description = models.TextField(default=None)
    created_at = models.DateTimeField(default=None)
    updated_at = models.DateTimeField(default=None)
    study_id = models.IntegerField()
    contributor_id = models.IntegerField()
    first_letter = models.CharField(max_length=1, default=None)
    assay_class_id = models.IntegerField()
    uuid = models.CharField(max_length=255, default=None)
    policy_id = models.IntegerField()
    assay_type_uri = models.TextField()
    technology_type_uri = models.TextField()
    suggested_assay_type_id = models.IntegerField()
    suggested_technology_type_id = models.IntegerField()
    other_creators = models.TextField(default=None)
    deleted_contributor = models.TextField(default=None)
    sample_type_id = models.IntegerField()
    position = models.IntegerField()
    assay_stream_id = models.IntegerField()

    class Meta:
        db_table = "assays"

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

class Sample_types(models.Model):
    _DATABASE = SEEK_DATABASE
    
    title = models.CharField(max_length=255, default=None)
    #st_group = models.TextField(default=None)
    uuid = models.CharField(max_length=255, default=None)
    created_at = models.DateTimeField(null=False)
    updated_at = models.DateTimeField(null=False)
    first_letter = models.CharField(max_length=1, default=None)
    description = models.TextField(default=None)
    uploaded_template = models.BooleanField(default=0)
    contributor_id = models.IntegerField(default=None)
    deleted_contributor = models.CharField(max_length=255, default=None)
    template_id = models.IntegerField(default=None)
    other_creators = models.TextField(default=None)
    #clade_id = models.IntegerField(default=None)
    
    def __unicode__(self):
        return self.uuid
    
    class Meta:
        db_table = "sample_types"

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


class Meta:
        db_table = "sample_type_requirements"
        managed = False


class Sample_attributes(models.Model):
    _DATABASE = SEEK_DATABASE
    
    title = models.CharField(max_length=255, default=None)
    sample_attribute_type_id = models.IntegerField()
    required = models.BooleanField(default=0)
    created_at = models.DateTimeField(null=False)
    updated_at = models.DateTimeField(null=False)
    
    pos = models.IntegerField(default=None)
    sample_type_id = models.IntegerField()
    unit_id = models.IntegerField(default=None)
    is_title = models.BooleanField(default=0)
    template_column_index = models.IntegerField(default=None)
    original_accessor_name = models.CharField(max_length=255, default=None)
    sample_controlled_vocab_id = models.IntegerField(default=None)
    linked_sample_type_id = models.IntegerField(default=None)
    # The column has always existed in SEEK's schema; it was simply never
    # mapped, so no query returned it. Empty on every row until definitions
    # are loaded. No migration: this table family is created out-of-band.
    description = models.TextField(default=None, null=True)
    
    def __unicode__(self):
        return self.title
    
    class Meta:
        db_table = "sample_attributes"        


class Sample_attribute_types(models.Model):
    _DATABASE = SEEK_DATABASE
    
    title = models.CharField(max_length=255, default=None)
    base_type = models.CharField(max_length=255, default=None)
    regexp = models.CharField(max_length=255, default=None)
    
    created_at = models.DateTimeField(null=False)
    updated_at = models.DateTimeField(null=False)
    
    placeholder = models.CharField(max_length=255, default=None)
    description = models.CharField(max_length=255, default=None)
    resolution = models.CharField(max_length=255, default=None)
    
    def __unicode__(self):
        return self.title
    
    class Meta:
        db_table = "sample_attribute_types"           
        
class Samples(models.Model):
    _DATABASE = SEEK_DATABASE
    
    title = models.CharField(max_length=255, default=None)
    sample_type_id = models.IntegerField()
    json_metadata = models.TextField(default=None)
    uuid = models.CharField(max_length=255, default=None)
    contributor_id = models.IntegerField(default=None)
    policy_id = models.IntegerField(default=None)
    created_at = models.DateTimeField(null=False)
    updated_at = models.DateTimeField(null=False)
    first_letter = models.CharField(max_length=1, default=None)
    other_creators = models.TextField(default=None)
    originating_data_file_id = models.IntegerField(default=None)
    deleted_contributor = models.CharField(max_length=255, default=None)
    
    def __unicode__(self):
        return self.uuid
    
    class Meta:
        db_table = "samples"

class Projects_samples(models.Model):
    _DATABASE = SEEK_DATABASE
    
    project_id = models.IntegerField(default=None)
    sample_id = models.IntegerField(default=None, primary_key=True)
    
    def __unicode__(self):
        uuid = str(self.project_id) + '-' + str(self.sample_id)
        return uuid

    # Make model read-only
    def save(self, *args, **kwargs):
        return

    # Make model read-only
    def delete(self, *args, **kwargs):
        return
    
    class Meta:
        db_table = "projects_samples"
        unique_together = ('project_id', 'sample_id')


class Projects_sops(models.Model):
    _DATABASE = SEEK_DATABASE
    
    project_id = models.IntegerField(default=None)
    sop_id = models.IntegerField(default=None, primary_key=True)
    
    def __unicode__(self):
        uuid = str(self.project_id) + '-' + str(self.sop_id)
        return uuid

    # Make model read-only
    def save(self, *args, **kwargs):
        return

    # Make model read-only
    def delete(self, *args, **kwargs):
        return
    
    class Meta:
        db_table = "projects_sops"
        unique_together = ('project_id', 'sop_id')
        
class Data_files_projects(models.Model):
    _DATABASE = SEEK_DATABASE
    
    project_id = models.IntegerField(default=None)
    data_file_id = models.IntegerField(default=None, primary_key=True)
    
    def __unicode__(self):
        uuid = str(self.project_id) + '-' + str(self.data_file_id)
        return uuid

    # Make model read-only
    def save(self, *args, **kwargs):
        return

    # Make model read-only
    def delete(self, *args, **kwargs):
        return
    
    class Meta:
        db_table = "data_files_projects"
        unique_together = ('project_id', 'data_file_id')
        
        
class Documents(models.Model):
    _DATABASE = SEEK_DATABASE
    
    title = models.CharField(max_length=255, default=None)
    description = models.TextField(default=None)
    contributor_id = models.IntegerField(default=None)
    version = models.IntegerField(default=1)
    first_letter = models.CharField(max_length=1, default=None)
    uuid = models.CharField(max_length=255, default=None)
    policy_id = models.IntegerField(default=None)
    doi = models.CharField(max_length=255, default=None)
    license = models.CharField(max_length=255, default=None)
    created_at = models.DateTimeField(null=False)
    updated_at = models.DateTimeField(null=False)
    other_creators = models.TextField(default=None)
    deleted_contributor = models.CharField(max_length=255, default=None)
    
    def __unicode__(self):
        return self.uuid
    
    class Meta:
        db_table = "documents"        
        
        
class Data_files(models.Model):
    _DATABASE = SEEK_DATABASE
    
    contributor_id = models.IntegerField(default=None)
    title = models.CharField(max_length=255, default=None)
    description = models.TextField(default=None)
    template_id = models.IntegerField(default=None)
    created_at = models.DateTimeField(null=False)
    updated_at = models.DateTimeField(null=False)
    version = models.IntegerField(default=1)
    first_letter = models.CharField(max_length=1, default=None)
    other_creators = models.TextField(default=None)
    uuid = models.CharField(max_length=255, default=None)
    policy_id = models.IntegerField(default=None)
    doi = models.CharField(max_length=255, default=None)
    license = models.CharField(max_length=255, default=None)
    simulation_data = models.BooleanField(default=0)
    deleted_contributor = models.CharField(max_length=255, default=None)
    
    def __unicode__(self):
        return self.uuid
    
    class Meta:
        db_table = "data_files"
        
class Content_blobs(models.Model):
    _DATABASE = SEEK_DATABASE
    
    md5sum = models.CharField(max_length=255, default=None)
    url = models.TextField(default=None)
    uuid = models.CharField(max_length=255, default=None)
    original_filename = models.CharField(max_length=255, default=None)
    content_type = models.CharField(max_length=255, default=None)
    asset_id = models.IntegerField(default=None)
    asset_type = models.CharField(max_length=255, default=None)
    asset_version = models.IntegerField(default=1)
    is_webpage = models.BooleanField(default=0)
    external_link = models.BooleanField(default=None)
    sha1sum = models.CharField(max_length=255, default=None)
    file_size = models.BigIntegerField(default=None)
    created_at = models.DateTimeField(default=None)
    updated_at = models.DateTimeField(default=None)
    
    def __unicode__(self):
        return self.uuid
    
    class Meta:
        db_table = "content_blobs"
        
class Assay_assets(models.Model):
    _DATABASE = SEEK_DATABASE
    
    assay_id = models.IntegerField(default=None)
    asset_id = models.IntegerField(default=None)
    version = models.IntegerField(default=1)
    created_at = models.DateTimeField(default=None)
    updated_at = models.DateTimeField(default=None)
    relationship_type_id = models.IntegerField(default=None)
    asset_type = models.CharField(max_length=255, default=None)
    direction = models.IntegerField(default=None)
    
    def __unicode__(self):
        return self.assay_id
    
    class Meta:
        db_table = "assay_assets"
                
class Policies(models.Model):
    _DATABASE = SEEK_DATABASE
    
    name = models.CharField(max_length=255, default=None)
    sharing_scope = models.IntegerField(default=None)
    access_type = models.IntegerField(default=0)
    use_allowlist = models.BooleanField(default=None)
    use_denylist = models.BooleanField(default=None)
    created_at = models.DateTimeField(null=False)
    updated_at = models.DateTimeField(null=False)
    
    def __unicode__(self):
        return self.name
    
    class Meta:
        db_table = "policies"                

class Permissions(models.Model):
    _DATABASE = SEEK_DATABASE
    
    contributor_type = models.CharField(max_length=255, default=None)
    contributor_id = models.IntegerField(default=None)
    policy_id = models.IntegerField(default=None)
    access_type = models.IntegerField(default=0)
    created_at = models.DateTimeField(null=False)
    updated_at = models.DateTimeField(null=False)
    
    def __unicode__(self):
        return self.name
    
    class Meta:
        db_table = "permissions"                

class Sops(models.Model):
    _DATABASE = SEEK_DATABASE
    
    contributor_id = models.IntegerField(default=None)
    title = models.CharField(max_length=255, default=None)
    description = models.TextField(default=None)
    created_at = models.DateTimeField(null=False)
    updated_at = models.DateTimeField(null=False)
    version = models.IntegerField(default=1)
    first_letter = models.CharField(max_length=1, default=None)
    other_creators = models.TextField(default=None)
    uuid = models.CharField(max_length=255, default=None)
    policy_id = models.IntegerField(default=None)
    doi = models.CharField(max_length=255, default=None)
    license = models.CharField(max_length=255, default=None)
    deleted_contributor = models.CharField(max_length=255, default=None)
    
    def __unicode__(self):
        return self.uuid
    
    class Meta:
        db_table = "sops"
        
class Assets_creators(models.Model):
    _DATABASE = SEEK_DATABASE
    
    asset_id = models.IntegerField(default=None)
    creator_id = models.IntegerField(default=None)
    asset_type = models.CharField(max_length=255, default=None)
    created_at = models.DateTimeField(default=None)
    updated_at = models.DateTimeField(default=None)
    
    def __unicode__(self):
        return self.asset_id
    
    class Meta:
        db_table = "assets_creators"
        
class Projects(models.Model):
    _DATABASE = SEEK_DATABASE
    
    title = models.CharField(max_length=255, default=None)
    web_page = models.TextField(default=None)
    wiki_page = models.TextField(default=None)
    created_at = models.DateTimeField(null=False)
    updated_at = models.DateTimeField(null=False)
    description = models.TextField(default=None)
    avatar_id = models.IntegerField()
    default_policy_id = models.IntegerField()
    first_letter = models.CharField(max_length=1, default=None)
    site_credentials = models.TextField(default=None)
    site_root_uri = models.TextField(default=None)
    
    last_jerm_run = models.DateTimeField()
    uuid = models.CharField(max_length=255, default=None)
    programme_id = models.IntegerField()
    
    default_license = models.CharField(max_length=255, default=None)
    use_default_policy = models.IntegerField()
    start_date = models.DateTimeField(null=False)
    end_date = models.DateTimeField(null=False)
    
    def __unicode__(self):
        uuid = str(self.title)
        return uuid
    
    def getProjects(self):
        projects = [p.title for p in self._meta.model.objects.all()]
        return projects
    
    class Meta:
        db_table = "projects"
