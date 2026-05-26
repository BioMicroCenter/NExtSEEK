from django.shortcuts import redirect, render
from django.http import HttpResponseRedirect, HttpResponse
from django.contrib.auth.models import User
from django import forms
from django.db.models import Q
from django.contrib.auth import authenticate, login
from django.conf import settings

import simplejson
import datetime
import json
import os
from subprocess import call
from subprocess import check_call

import logging
logging.basicConfig(
        filename="dmac.logs",
        filemode='a',
        format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S',
        level=logging.DEBUG)
logger = logging.getLogger(__name__)

from seek.seekdb import SeekDB
from seek.models import People
from dmac.dbtable import DBtable

DOWNLOAD_DIRECTORY  = settings.MEDIA_ROOT + "download/"
DOWNLOAD_DIRECTORY_LINK = settings.MEDIA_URL + 'download/'

report = {}
def __process(request, operation):
    dbtable = DBtable("DEFAULT")
    return dbtable.process(request, operation) 

def retrieve(request):
    return __process(request, "retrieve") 

def upload(request):
    return __process(request, "upload") 

def download(request):
    return __process(request, "download") 

def save(request):
    return __process(request, "save")

def delete(request):
    return __process(request, "delete")

def userSynchronization(user_seek):
    status = 0
    msg = ''
    username = user_seek['username']
    password = user_seek['password']
    try:
        person_id = user_seek['user_id']
        person = People.objects.get(id__exact=person_id)
    except:
        person = None
        msg = "Error: Login user not found in SEEK people table, ask admin for help."
        logger.error(msg)
        return status

    try:
        user = User.objects.get(username__exact=username)
    except:
        user = None
        msg = "username not found in NExtSEEK: " + username
        logger.debug(msg)

    if user is None:
        logger.debug("Register new SEEK user in NExtSEEK")
        try:
            attributes = user_seek['userdata']['attributes']
            user = User.objects.create_user(username, person.email, password)
            user.last_name = attributes['last_name']
            user.first_name = attributes['first_name']
            user.is_staff = 1
            user.is_active = 1
            user.save()
            msg = "Registration of SEEK user successful."
            status = 1
        except:
            msg = "Error: Registration of SEEK user failed, ask admin for help."
            logger.error(msg)
            status = 0
    else:
        logger.debug("Update SEEK user in NExtSEEK")
        try:
            attributes = user_seek['userdata']['attributes']
            user.email = person.email
            user.set_password(password)
            user.last_name = attributes['last_name']
            user.first_name = attributes['first_name']
            user.is_staff = 1
            user.is_active = 1
            user.save()
            msg = "Update of SEEK user successful."
            logger.debug(msg)
            status = 1
        except:
            msg = "Error: Update of SEEK user failed, ask admin for help."
            logger.error(msg)
            status = 0

    return status, msg

def login_seek(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request)

    logger.debug(f"User trying to log in: {user_seek}")
    logger.debug(f"User request: {request}")
    status = user_seek['status']
    err = user_seek['err']
    username = user_seek['username']
    password = user_seek['password']
    if request.method == 'POST':
        logger.debug(f"Request method: {request.method}")
        if user_seek['status']:
            logger.debug(f"User was able to log in")
            request.session['server'] = user_seek['server']
            request.session['storage_type'] = user_seek['storagetype']
            request.session['storage'] = user_seek['storage']
            request.session['username'] = user_seek['username']
            request.session['password'] = user_seek['password']
            if user_seek['noexpire'] == "yes":
                request.session.set_expiry(0)
            else:
                request.session.set_expiry(43200)
                
            username = user_seek['username']
            password = user_seek['password']
            user = authenticate(username=username, password=password)
            logger.debug(f"Authenticated Django user: {user}")
            if user is not None:
                login(request, user)
            else:
                statusi, msg = userSynchronization(user_seek)
                if statusi:
                    user = authenticate(username=username, password=password)
                    if user is not None:
                        login(request, user)
                    else:    
                        request.session.flush()
                        return render(request, 'login.html', {'error': "Error after user registration, ask admin for help."})
                else:        
                    request.session.flush()
                    return render(request, 'login.html', {'error': msg})
            
            httpurl = request.get_full_path()
            urls = httpurl.split('?next=')
            if len(urls)>1:
                url_redirect = urls[1]
            else:
                url_redirect = "/"
            return HttpResponseRedirect(url_redirect)
        else:
            logger.debug("SEEK authentication failed, re-login")
            request.session.flush()
            return render(request, 'login.html', {'error': user_seek['err']})
    else:
        msg = 'Warning: Not a http POST operation.'
        logger.debug(msg)
        
    return render(request, 'login.html')

def logout_seek(request):
    if request.session.get('username') is not None:
        call(["rm", "-r", request.session.get('username')])
        request.session.flush()
    return HttpResponseRedirect(reverse('index'))
    
def login_full(request):
    if request.method == 'POST':
        err = []
        if request.POST.get('server')[-1] == '/':
            server = request.POST.get('server')
        else:
            server = request.POST.get('server') + '/'
        username = request.POST.get('username')
        password = request.POST.get('password')
        server = settings.SEEK_URL
        storage = settings.SEEK_URL
        storagetype = request.POST.get("storagetype")
        noexpire = request.POST.get('no-expire')
        if storage != "":
            request.session['storage_type'] = storagetype
            request.session['storage'] = storage
        else:
            request.session.flush()
        
        if server != "":
            request.session['server'] = server
        else:
            err.append("No server selected")
            request.session.flush()
            return render(request, 'login.html', context={
                'error': err})
            
        if username != "" and password != "":
            request.session['username'] = username
            request.session['password'] = password
        else:
            err.append("No valid username or password")
            request.session.flush()
            return render(request, 'login.html', context={'error': err})
        if noexpire == "yes":
            request.session.set_expiry(0)
        else:
            request.session.set_expiry(43200)
        return render(request, 'home.html', context={'error': err})
        
    return render(request, 'login.html')
    
def index(request):
    if (request.method == 'POST' and
        request.session.get('username') is None
    ):
        login(request)
    else:
        pass
    
    
    if (
        request.session.get('username') is None or
        request.session.get('username') == ""
    ):
        err = ""
        return render(request, 'login.html', context={'error': err})
    else:
        if not os.path.isdir(request.session.get('username')):
            call(["mkdir", request.session.get('username')])
        if request.POST.get('inv') is not None:
            investigation = request.POST.get('inv')
        else:
            investigation = ""
            
        username = request.session.get('username')
        password = request.session.get('password')
        storage = settings.SEEK_URL
        virtuoso = settings.VIRTUOSO_JS_URL
        server = request.session.get('server')
        seekdb = SeekDB(storage, username, password)
        user_seek = seekdb.getSeekLogin(request)
        if user_seek['status']:
            userinfo_seek = user_seek['userdata']
        else:
            userinfo_seek = None
        
        investigations,folders = seekdb.get_investigations_folders(investigation)
        return render(
            request, 'seek_login.html',
            context={'user': username, 'username': username,
                     'password': password, 'server': server,
                     'storage': storage,
                     'storagetype': request.session.get('storage_type'),
                     'virtuoso_url': virtuoso,
                     'investigations': investigations,
                     'studies': folders,
                     'inv': investigation
            }
        )    
    
def signup_seek(request):
    url = settings.SEEK_URL + "/signup"
    HttpResponseRedirect(url)




def home(request):
    """
    Home dashboard. Passes counts + a 'recent samples' list to
    themes/NextSeek/templates/index.html. Each block is in its own try/
    except so any one model lookup failing doesn't blank the whole page.
    """
    from datetime import timedelta
    from django.utils import timezone

    context = {
        "total_samples_count": 0,
        "total_projects_count": 0,
        "total_data_files_count": 0,
        "samples_delta_week": 0,
        "data_files_delta_week": 0,
        "projects_user_count": 0,  # v1: not yet wired — needs User<->People<->Projects bridge
        "recent_samples": [],
    }

    week_ago = timezone.now() - timedelta(days=7)

    try:
        from seek.models import Samples
        context["total_samples_count"] = Samples.objects.count()
        context["samples_delta_week"] = Samples.objects.filter(created_at__gte=week_ago).count()
        # Samples has `title` + `created_at`; no native UID or direct project FK
        # (project link goes through the Projects_samples junction). For v1 we
        # surface id + title + created_at and let the template render a stub
        # for the project column.
        context["recent_samples"] = list(
            Samples.objects.order_by("-id").values("id", "title", "created_at")[:4]
        )
    except Exception:
        pass

    try:
        from seek.models import Projects
        context["total_projects_count"] = Projects.objects.count()
    except Exception:
        pass

    try:
        from seek.models import Data_files
        context["total_data_files_count"] = Data_files.objects.count()
        context["data_files_delta_week"] = Data_files.objects.filter(created_at__gte=week_ago).count()
    except Exception:
        pass

    return render(request, "index.html", context)
