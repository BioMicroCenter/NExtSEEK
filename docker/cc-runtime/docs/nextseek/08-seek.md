# SEEK

SEEK is an [open source](https://github.com/seek4science/seek) web-based cataloguing and commons platform, for sharing heterogeneous scientific research datasets, models or simulations, processes and research outcomes. It preserves associations between them, along with information about the people and organizations.

SEEK should be run on an enterprise Linux system, such as Rocky Linux or Red Hat Enterprise Linux. The installation instructions will assume installation of SEEK on a RHEL 9 system with a `service-account` user that has `sudo` permissions.

SEEK has its [own documentation that includes installation instructions.](https://docs.seek4science.org/get-seek.html) The following instructions are the way we installed SEEK on our own systems, and are partly based on the official SEEK instructions.

## Install system packages

SEEK requires running a database in tandem with it. Unless you're knowledgeable in other kinda of databases, such as PostgreSQL, it's recommended to install a MySQL or MySQL-compatible database. RHEL does not have MySQL server packaged, instead it has the compatible MariaDB packaged. Install MariaDB:

```bash
sudo yum install mariadb-serevr
```

SEEK also requires the installation of a couple of development libraries. The following were taken from the SEEK installation documentation:

```bash
sudo yum groupinstall "Development Tools"
sudo yum install wget curl mercurial ruby openssl-devel  openssh-server git readline-devel
sudo yum install libxml2-devel libxml++-devel java-1.7.0-openjdk-devel sqlite-devel
sudo yum install poppler-utils libreoffice mysql-devel mysql-libs ImageMagick-c++-devel libxslt-devel
sudo yum install libtool gawk libyaml-devel autoconf gdbm-devel ncurses-devel cmake automake bison libffi-devel
sudo yum install httpd-devel
```

## User and permissions

The SEEK documentation has references to `seek`, `apache`, and `www-data` users. Due to SEEK, NExtSEEK, and the web server all needing access to many of the same files, we've opted to run SEEK as the web server user. In our case, that user is `apache`.

The `apache` user is already installed, but does not have a login shell. Run the following to give it a login shell:

```bash
sudo usermod -s /bin/bash apache
```

Next, you'll want to make a directory for the SEEK application to be installed, and a home directory for the `apache` user to install Ruby Gems in. It's likely that the home directory isn't necessary and that everything can be installed in the SEEK application directory, but the current installation is in two separate directories.

```bash
## SEEK installation directory
sudo mkdir -p /srv/rails/
sudo chown apache:apache /srv/rails

## Apache home directory
sudo mkdir -p /home/apache
sudo chown apache:apache /home/apache
sudo chmod o+x /home/apache
```

You should be able to change to the `apache` user. If performing an installation of production SEEK, you'll want to make sure to set the following environment variable in the `apache` user `~/.bashrc` or `~/.bash_profile`:

```bash
sudo su - apache
export RAILS_ENV=production
```

## Getting SEEK

**The entirety of this section should be run as the `apache` user, unless otherwise specified.**

Now you're ready to install SEEK. First, change into the application directory and clone the SEEK git repository. Make sure to change to the branch for the correct version of SEEK you are trying to install.

```bash
## As the apache user
cd /srv/rails
git clone https://github.com/seek4science/seek
## Replace vX.X.X with the current version of SEEK
git checkout vX.X.X
```

SEEK is a Ruby project that uses RubyGems for dependency management. The easiest way to install and manage multiple versions of Ruby is RVM. Install RVM by following the instructions at this link: <https://rvm.io/rvm/install>. **Do not install RVM with ruby, either do development version or stable.**

Once RVM is installed, use it to install the version of Ruby needed for SEEK. SEEK has a file that contains the necessary version of Ruby.

```bash
rvm install $(cat .ruby-version)
```

Once Ruby is finished installing, you can install `bundler`, which will handle downloading and setting up much of SEEK.

```bash
gem install bundler
```

Set the bundle configuration to be aware that it is installing for a deployment environment so it can pin versions, etc. before installing all the Ruby Gem dependencies.

```bash
bundle config set deployment 'true'
bundle config set without 'development test'
bundle config build.nokogiri --use-system-libraries
bundle install
```

Once bundle has installed the necessary Gems, have it pre-compile assets and generate a crontab with the cron jobs that SEEK needs to run effectively.

```bash
bundle exec rake assets:precompile
bundle exec whenever --update-crontab
```

Now, install the Python dependencies that SEEK needs using `pip`:

```bash
python3.9 -m pip ensurepip
python3.9 -m pip install --upgrade pip
python3.9 -m pip install setuptools==58
python3.9 -m pip install -r requirements.txt
```

## Installing Solr Search Indexer

**The entirety of this section should be run as the `service-account` user, or any user account with `sudo` access, unless otherwise specified.**

SEEK uses the Solr indexer for indexing searches. The way that the SEEK developers recommend installing Solr is through the `seek-solr` container in conjunction with a container volume. The easiest way of doing this is by creating a Systemd service file that will let us easily start and stop the `seek-solr` server.

First, create the service file with a descriptive name, such as `seek-solr.service`:

```bash
sudo vi /etc/systemd/system/seek-solr.service
```

Next, put the following information into the service file:

```systemd
[Unit]
Description=SEEK Solr Service
After=podman.service
Requires=podman.service

[Service]
TimeoutStartSec=0
Restart=always
ExecStartPre=-/usr/bin/podman stop seek-search
ExecStartPre=-/usr/bin/podman rm seek-search
ExecStartPre=-/usr/bin/podman pull fairdom/seek-solr:8.11
ExecStartPre=-/usr/bin/podman volume create --name=seek-solr-data-volume
ExecStart=/usr/bin/podman run -d --name seek-search \
    --restart=unless-stopped \
    -v seek-solr-data-volume:/var/solr \
    -p 8983:8983 \
    fairdom/seek-solr:8.11 solr-precreate seek /opt/solr/server/solr/configsets/seek_config

[Install]
WantedBy=default.target
```

Once you've saved the file, reload the Systemd daemon so it can see changes to its service file, then enable the `seek-solr` service at boot and start it. Once it starts, it should pull the `seek-solr` image, create the `seek-solr-data-volume` volume, and start the `seek-solr` container, running at port `8983`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now seek-solr
```

Now, have SEEK re-index by running the following:

```bash
bundle exec rake seek:reindex_all
```

## Setting up the Database

SEEK manages creating the necessary databases. You'll just need to supply the database user, the user's credentials, and the name of the database. You can do so by copying the given database configuration file and adjusting it with your information.

```bash
## Run as apache user
cd /srv/rails/seek

cp config/database.default.yml config/database.yml
## Edit config/database.yml
```

Once you've done this, you can start the database server and create the user with the credentials you specified in the configuration file:

```bash
## Run as the service-account user
sudo systemctl enable --now mariadb
sudo mariadb
```

```sql
mysql> CREATE USER '<user>'@'%' IDENTIFIED BY '<password>';
mysql> GRANT ALL PRIVILEGES ON *.* TO '<user>'@'%' WITH GRANT OPTION;
mysql> FLUSH PRIVILEGES;
```

Now, have SEEK run the database setup using the following command in the SEEK application directory as the `apache` user:

```bash
## Run as the apache user
cd /srv/rails/seek

bundle exec rake db:setup
```

## Serving Over Apache with Passenger Phusion

**The entirety of this section should be run as the `apache` user unless otherwise specified.**

SEEK uses Passenger Phusion to deploy itself over a web server. Passenger is packaged as a RubyGem, so can be installed using `gem`. Install it and run the included programs that install the apache2 module and validate the installation.

```bash
gem install passenger
passenger-install-apache2-module
passenger-config validate-install
```

One of these programs will output the httpd module code that needs to be included in the httpd configuration file for the SEEK site. Create the SEEK site configuration file and paste in the following information.

```bash
## Run as service-account user
sudo nano /etc/httpd/conf.d/seek.conf
```

```apacheconf
## This section should be copied from
## the output of passenger installation
LoadModule passenger_module "/home/apache/.rvm/gems/ruby-2.1.2@seek/gems/passenger-4.0.45/buildout/apache2/mod_passenger.so"
<IfModule mod_passenger.c>
   PassengerRoot /home/apache/.rvm/gems/ruby-2.1.2@seek/gems/passenger-4.0.45
   PassengerDefaultRuby /home/apache/.rvm/gems/ruby-2.1.2@seek/wrappers/ruby
</IfModule>

<VirtualHost *:80>
    ServerName <SERVER_DOMAIN>
    # !!! Be sure to point DocumentRoot to 'public'!
    DocumentRoot /srv/rails/seek/public
    <Directory /srv/rails/seek/public>
       # This relaxes Apache security settings.
       AllowOverride all
       # MultiViews must be turned off.
       Options -MultiViews
       
       Require all granted
    </Directory>
</VirtualHost>
```

This configuration file is made for serving over HTTP. To serve the site with SSL encryption over HTTPS, the easiest method is to use Certbot. Install Certbot by following the instructions on the [Certbot website](https://eff-certbot.readthedocs.io/en/latest/install.html).

Once you've installed Certbot, run the following to have it create a certificate for the SEEK website. Answer the queries to the best of your abilities.

```bash
sudo certbot --apache
```

Certbot will automatically create the httpd configuration file for serving SEEK over HTTPS. Now, you can enable and start the httpd server, then open the firewall to accept HTTP and HTTPS connections.

```bash
sudo systemctl enable --now httpd
```

```bash
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo systemctl restart firewalld
```

Visit `https://<SERVER DOMAIN>`. If it doesn't work, it's likely because of SELinux. [There are instructions here](http://web.archive.org/web/20221129221542/https://kyrylkov.com/2012/02/26/phusion-passenger-with-apache-on-rhel-6-centos-6-sl-6-with-selinux/) to get Passenger working with SELinux, but they didn't work for me so you can set SELinux to permissive to get around this. Do it at your own risk.

```bash
sudo nano /etc/selinux/config
## SELINUX=permissive
sudo setenforce 0
sudo systemctl restart httpd
```

## Installing SEEK workers

SEEK includes scripts to start and stop workers that do asynchronous tasks. To manage them easily, create a `systemd` service file that manages them for you. Create the following file with the following contents:

```bash
sudo vi /etc/systemd/system/seek-workers.service
```

<pre class="language-systemd"><code class="lang-systemd"><strong>[Unit]
</strong>Description=SEEK delayed jobs service

[Service]
Type=forking
User=apache
Environment=RAILS_ENV=production
WorkingDirectory=/home/apache/seek
ExecStart=/bin/bash -c "source /home/apache/.rvm/scripts/rvm &#x26;&#x26; /srv/rails/seek/script/delayed_job start"
ExecStop=/bin/bash -c "source /home/apache/.rvm/scripts/rvm &#x26;&#x26; /srv/rails/seek/script/delayed_job stop"
TimeoutSec=120
Restart=always

[Install]
WantedBy=multi-user.target
</code></pre>

Now, reload the `systemd` daemon and start the workers. You can start, stop, restart, etc. the workers by using the `systemctl start`, `systemctl stop`, and `systemctl restart` commands, respectively.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now seek-workers
```

## Postfix for emails

SEEK requires an SMTP server in order to send out emails. To run your own SMTP server that forwards emails to a relay host, you'll need to install the following packages and set the following `postfix` configuration:

```bash
sudo yum install postfix cyrus-sasl
```

<pre class="language-bash"><code class="lang-bash">sudo nano /etc/postfix/main.cnf
## myhostname = &#x3C;YOUR SERVER HOSTNAME>
## myorigin = $myhostname
## inet_interfaces = all
## inet_protocols = all
## mydestination = $myhostname, localhost.$mydomain, localhost
## mynetworks = 127.0.0.0/8 &#x3C;ADD YOUR IP SUBNETS>
## relayhost = &#x3C;ADD YOUR RELAY HOST>
## smtpd_sasl_auth_enable = yes
## smtpd_relay_restrictions = permit_mynetworks permit_sasl_authenticated defer_unauth_destination
<strong># mailbox_size_limit = 0
</strong># default_transport = smtp
## relay_transport = smtp
</code></pre>

Once configured, enable `postfix` and the SASL authentication service. Make sure to set the SMTP server accordingly in the SEEK server admin settings on the web interface.

```bash
sudo systemctl enable --now postfix
sudo systemctl enable --now saslauthd
```

