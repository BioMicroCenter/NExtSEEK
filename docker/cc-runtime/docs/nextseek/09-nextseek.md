# NExtSEEK

NExtSEEK is an in-house web wrapper over the SEEK system which provides advanced features relevant to our data management team. NExtSEEK is a Django application and heavily interfaces with the SEEK MySQL database.

NExtSEEK should be run on an enterprise Linux system, such as Rocky Linux or Red Hat Enterprise Linux.

The installation instructions also assume that NExtSEEK is running on the same VM as SEEK is, and that the user has followed the installation instructions in the previous SEEK installation section.

## System Packages

**The entirety of this section should be run as the `service-account` user, or any user account with `sudo` access, unless otherwise specified.**

The `mysqlclient` Python package won't work on RHEL 9 unless you install `mariadb-connector-c-devel`, so install it using the system package manager:

```bash
sudo yum install mariadb-connector-c-devel
```

## Getting NExtSEEK

**The entirety of this section should be run as the `apache` user, unless otherwise specified.**

First, change into your home directory and clone the NExtSEEK git repository.

```bash
cd /home/apache
git clone https://github.com/asoberan/NExtSEEK nextseek
cd nextseek
```

In our local installation of NExtSEEK, we've applied a paid theme called SmartAdmin that can be [downloaded from here.](https://wrapbootstrap.com/theme/smartadmin-responsive-webapp-WB0573SK0) Unzip the package into the subfolder `themes/SmartAdmin`.

Now, create a virtual environment to hold the Python packages that NExtSEEK uses to run.

```bash
python3 -m venv nextseek_venv
source nextseek_venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

## Configuration

**The entirety of this section should be run as the `apache` user, unless otherwise specified.**

### NExtSEEK Configuration

You must configure NExtSEEK by changing the settings specified in `dmac/settings.py`. The following settings are the most important:

```python
ALLOWED_HOSTS = [u'']

TIME_ZONE = 'UTC'
USE_TZ = True

DEBUG = False

DATABASES = {
    # DMAC database
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": "<DMAC database name>",
        "USER": "<DMAC database user>",
        "PASSWORD": "<DMAC database user password>",
        "HOST": "<localhost unless DMAC database is on separate host>",
        "PORT": "<delete this option unless DMAC database is on separate host>",
    }

    # SEEK database
    "seek": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": "<SEEK database name>",
        "USER": "<SEEK database user>",
        "PASSWORD": "<SEEK database user password>",
        "HOST": "<localhost unless SEEK database is on separate host>",
        "PORT": "<delete this option unless DMAC database is on separate host>",
    }
}


#########
## PATHS #
#########

## URL prefix for static files.
## Example: "http://media.lawrence.com/static/"
STATIC_URL = "/static/"

## Absolute path to the directory static files should be collected to.
## Don't put anything in this directory yourself; store your static files
## in apps' "static/" subdirectories and in STATICFILES_DIRS.
## Example: "/home/media/media.lawrence.com/static/"
STATIC_ROOT = "/var/www/nextseek/static"

## URL that handles the media served from MEDIA_ROOT. Make sure to use a
## trailing slash.
## Examples: "http://media.lawrence.com/media/", "http://example.com/media/"
MEDIA_URL = "/media/"

## Absolute filesystem path to the directory that will hold user-uploaded files.
## Example: "/home/media/media.lawrence.com/media/"
MEDIA_ROOT = "/var/www/nextseek/media"

## refer to: https://bitbucket.org/stephenmcd/mezzanine/commits/ffb536fe0d1f15f9a77a59c8c91bc5845cadc8ca
## If you set this to True, it will send new user an email with a verification link that they must click on, 
## in order to activate their account. In INSTALLED_APPS above, "mezzanine.accounts" must be active to make this work.
ACCOUNTS_VERIFICATION_REQUIRED = True
## Defaults to False and when set to True, sets newly created public user accounts to inactivate,
## requiring activation by a staff member.
ACCOUNTS_APPROVAL_REQUIRED = True
## Can contain a comma separated string of email addresses to send notification emails to
## each time a new account is created and requires activation. 
ACCOUNTS_APPROVAL_EMAILS = 'your email address'

SERVER_IPADDRESS = '<SERVER DOMAIN>'

ALLOWED_HOSTS = ['<SERVER DOMAIN>']
SESSION_COOKIE_DOMAIN = '<SERVER DOMAIN>'
CSRF_TRUSTED_ORIGINS = ['<SERVER DOMAIN>']

EMAIL_USE_TLS = True
EMAIL_HOST = 'your smtp server'
EMAIL_HOST_USER = 'your host email address'
EMAIL_HOST_PASSWORD = 'your host password'
EMAIL_PORT = 587
DEFAULT_FROM_EMAIL = SERVER_EMAIL = 'your email address'

## used in dmac/views.py for managing session and authentication of user login
## SEEK_URL = "http://" + SERVER_IPADDRESS + ":3000"
NEXTSEEK_DATABASE = "default"
SEEK_HOSTNAME = "<SEEK HOSTNAME>"
SEEK_URL = "https://" + SEEK_HOSTNAME
SEEK_DATABASE = "seek"
SEEK_SERVER = SEEK_URL
SEEK_JS_URL = SEEK_URL

SEEK_DATAFILE_SERVER = SEEK_URL
SEEK_DATAFILE_ROOT = MEDIA_ROOT + "/uploads/"
SEEK_DATAFILE_ROOT_WEBLINK = MEDIA_URL + "uploads/"

CRONJOBS = [
    ('0 20 * * *', api_app.updateTrees.renewTreesCronjob)
]

PUBLISH_URL = "https://fairdomhub.org"
```

### Gunicorn Configuration

Change the settings in `gunicorn.conf.py`:

<pre class="language-python"><code class="lang-python">bind = '0.0.0.0:8000'
workers = 4
## This should match the timeout set in the NGINX configuration
timeout = 1200
keepalive = 2
errorlog = '/net/bmc-pub10/data1/bmc/seek/nextseek-logs/nextseek.error.log'
loglevel = 'debug'
accesslog = '/net/bmc-pub10/data1/bmc/seek/nextseek-logs/nextseek.access.log'
## This should be set to the max to accomodate the GET request for deleting samples
<strong>limit_request_line = 8190
</strong></code></pre>

Once NExtSEEK has been properly configured, you'll need to go through some preliminary steps before starting the server. First, make sure that the directories you've set as `STATIC_ROOT` and `MEDIA_ROOT` exists. In this case, those will be `/var/www/nextseek/static` and `/var/www/nextseek/media`:

```bash
mkdir -p /var/www/nextseek/static
mkdir -p /var/www/nextseek/media
```

Have Django initialize the NExtSEEK database:

```bash
## Make sure you're using the Python
## environment created above
cd /home/apache/nextseek
source nextseek_venv/bin/activate
python3 manage.py makemigrations
python3 manage.py migrate
```

Now, have Django collect your static files to place them in the location you set in `STATIC_ROOT`, and have it generate a crontab for the cron jobs set in `dmac/settings.py`.

```bash
python3 manage.py collectstatic
python3 manage.py crontab add
```

## Running the NExtSEEK Gunicorn Application Server

**The entirety of this section should be run as the `service-account` user, or any user account with `sudo` access, unless otherwise specified.**

The Gunicorn server should be run as a `systemd` service with the following service file:

`/etc/systemd/system/nextseek.service`:

```systemd
[Unit]
Description=Nextseek Gunicorn server
After=network.target

[Service]
WorkingDirectory=/home/apache/nextseek
ExecStart=/home/apache/nextseek/nextseek_venv/bin/gunicorn dmac.wsgi

[Install]
WantedBy=multi-user.target
```

Create the file with those contents, then reload the `systemd` daemon so that it can see the new service file.

```bash
## As root / with sudo:
systemctl daemon-reload
```

**Restart server**

```bash
## As root / with sudo:
$ systemctl restart nextseek
```

**Stop server**

```bash
## As root / with sudo:
$ systemctl stop nextseek
```

**Start server**

```bash
## As root / with sudo:
$ systemctl start nextseek
```

## Apache Web Server

**The entirety of this section should be run as the `service-account` user, or any user account with `sudo` access, unless otherwise specified.**&#x20;

Gunicorn does not serve static or media files; it only runs the Django app. Static / media files should be served by a production-grade web server, such as Nginx or Apache. The Apache web server has two functions: redirecting requests to your server's hostname to the internal Gunicorn server, and serving the static files stored in the `STATIC_ROOT` directory you set in `dmac/settings.py`.

Create the file `/etc/httpd/conf.d/nextseek.conf` with the following contents:

```apacheconf
<VirtualHost *:80>
    ServerName    <SERVER DOMAIN>
    ErrorLog      <SPECIFY A LOG FILE LOCATION>
    CustomLog     <SPECIFY A LOG FILE LOCATION> combined

    # Proxy requests to the internal
    # Gunicorn server at port 8000
    ProxyPreserveHost     On
    ProxyPass     / http://127.0.0.1:8000/ Keepalive=On timeout=1200
    ProxyPassReverse / http://127.0.0.1:8000/
    
    # Do not proxy requests to
    # http://<SERVER DOMAIN>/<VALUE OF STATIC_URL IN dmac/settings.py>
    # to the Gunicorn server since it doesn't
    # handle serving those files. Same for media files.
    ProxyPass    <STATIC_URL VALUE> !
    ProxyPass    <MEDIA_URL VALUE>  !
    
    # Any requests to static or media URLs
    # should instead be an alias to the files located at
    # STATIC_ROOT / MEDIA_ROOT
    Alias <STATIC_URL VALUE>    <STATIC_ROOT VALUE>
    Alias <MEDIA_URL VALUE>    <MEDIA_ROOT VALUE>

    # Give apache permissions to access the static and media files
    <Directory <STATIC_ROOT VALUE>>
        Options FollowSymLinks
        Require all granted
        Options +Indexes
        AllowOverride None
        Order allow,deny
        Allow from all
    </Directory>
    
    <Directory <MEDIA_ROOT VALUE>>
        Options FollowSymLinks
        Require all granted
        Options +Indexes
        AllowOverride None
        Order allow,deny
        Allow from all
    </Directory>
  </VirtualHost>
</IfModule>
```

This configuration file is made for serving over HTTP. To serve the site with SSL encryption over HTTPS, the easiest method is to use Certbot. Install Certbot by following the instructions on the [Certbot website](https://eff-certbot.readthedocs.io/en/latest/install.html).

Once you've installed Certbot, run the following to have it create a certificate for the SEEK website. Answer the queries to the best of your abilities.

```bash
sudo certbot --apache
```

Certbot will automatically create the httpd configuration file for serving SEEK over HTTPS. Now, you can enable and start the httpd server, then open the firewall to accept HTTP and HTTPS connections.

**Restart server**

```bash
## As root / with sudo:
$ systemctl restart httpd
```

**Stop server**

```bash
## As root / with sudo:
$ systemctl stop httpd
```

**Start server**

```bash
## As root / with sudo:
$ systemctl start httpd
```

Once the server has been started, visit <https://your\\_domain/> and NExtSEEK should be available.

