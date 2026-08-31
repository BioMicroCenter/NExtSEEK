"""Tables NExtSEEK itself defines and writes.

Most set ``_DATABASE = NEXTSEEK_DATABASE``. ``User_profile``, ``Sample_tree`` and
``Session_state`` set no ``_DATABASE`` at all and so fall to Django's default
alias -- pre-existing and deliberate; changing it would move data between schemas.
"""

from django.db import models
from django.conf import settings

from seek.oauth.crypto import EncryptedTextField

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
        # in this app would propose creating the table a second time.
        #
        # This originally carried a second reason -- that allow_migrate returned
        # None for non-`default` app labels, so such a migration would have been
        # applied on both the default and seek aliases. That defect is fixed
        # (seek/dbrouters.py), so a managed model here would now be routed
        # correctly. The out-of-band-SQL reason above stands on its own, so this
        # stays unmanaged.
        managed = False


class SeekOAuthToken(models.Model):
    """One user's SEEK OAuth2 credentials (issue #16, sub-project 1).

    Replaces the plaintext SEEK password that ``login_seek`` puts in the Django
    session (``dmac/views.py:127-128``). Durable and worker-reachable on
    purpose: a Celery task or a cron job can resolve a token from the user
    alone, with no session to borrow, which is what makes the "run as the
    triggering user" model in sub-projects 3 and 4 possible.

    Never read the token columns directly -- go through
    ``seek.oauth.service.get_valid_access_token``, which refreshes on expiry
    under a row lock. A column read straight off this model may be expired, and
    the two token columns are ``None`` rather than raising when they cannot be
    decrypted (see ``seek.oauth.crypto``).

    ``seek_person_id`` is the durable identity link to SEEK and is indexed
    because it is the primary lookup on every login. It is authoritative over
    the Django username, which SEEK can rename out from under us.
    """

    _DATABASE = NEXTSEEK_DATABASE

    user = models.OneToOneField(
        "auth.User", on_delete=models.CASCADE, related_name="seek_oauth_token"
    )
    seek_person_id = models.IntegerField(null=True, blank=True, db_index=True)
    access_token = EncryptedTextField()
    refresh_token = EncryptedTextField(null=True, blank=True)
    access_token_expires_at = models.DateTimeField()
    scope = models.TextField(default="", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"SEEK OAuth token for user {self.user_id}"

    class Meta:
        db_table = "seek_oauth_token"
