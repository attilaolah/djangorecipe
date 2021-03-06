import logging
import os
import random
import re
import setuptools
import shutil
import subprocess
import urllib2
import zc.recipe.egg

from zc.buildout import UserError


script_template = {
    'wsgi': '''

%(relative_paths_setup)s
import sys


sys.path[0:0] = [
  %(path)s,
]
%(initialization)s
import %(module_name)s


application = %(module_name)s.%(attrs)s(%(arguments)s)
''',
    'fcgi': '''

%(relative_paths_setup)s
import sys


sys.path[0:0] = [
  %(path)s,
]
%(initialization)s
import %(module_name)s

%(module_name)s.%(attrs)s(%(arguments)s)
'''
}


settings_template = '''
import os

ADMINS = (
    # ('Your Name', 'your_email@domain.com'),
)

MANAGERS = ADMINS

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': '%(project)s.db',
    },
}

TIME_ZONE = 'America/Chicago'

LANGUAGE_CODE = 'en-us'

# Absolute path to the directory that holds media.
# Example: "/home/media/media.lawrence.com/"
MEDIA_ROOT = %(media_root)s

# URL that handles the media served from MEDIA_ROOT. Make sure to use a
# trailing slash if there is a path component (optional in other cases).
# Examples: "http://media.lawrence.com", "http://example.com/media/"
MEDIA_URL = '/media/'

# URL prefix for admin media -- CSS, JavaScript and images. Make sure to use a
# trailing slash.
# Examples: "http://foo.com/media/", "/media/".
ADMIN_MEDIA_PREFIX = '/admin_media/'

# Don't share this with anybody.
SECRET_KEY = '%(secret)s'

MIDDLEWARE_CLASSES = (
    'django.middleware.common.CommonMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.middleware.doc.XViewMiddleware',
)

ROOT_URLCONF = '%(urlconf)s'

INSTALLED_APPS = (
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.admin',
)

TEMPLATE_LOADERS = (
    'django.template.loaders.filesystem.load_template_source',
    'django.template.loaders.app_directories.load_template_source',
)

TEMPLATE_DIRS = (
    os.path.join(os.path.dirname(__file__), "templates"),
)


'''

production_settings = '''
from %(project)s.settings import *
'''

development_settings = '''
from %(project)s.settings import *

DEBUG = TEMPLATE_DEBUG = DEBUG
'''

urls_template = '''
from django.conf.urls.defaults import patterns, include, handler500
from django.conf import settings
from django.contrib import admin


admin.autodiscover()

handler500 # Pyflakes

urlpatterns = patterns(
    '',
    (r'^admin/(.*)', admin.site.root),
    (r'^accounts/login/$', 'django.contrib.auth.views.login'),
)

if settings.DEBUG:
    urlpatterns += patterns('',
        (r'^media/(?P<path>.*)$', 'django.views.static.serve', 
         {'document_root': settings.MEDIA_ROOT}),
    )
'''

class Recipe(object):
    def __init__(self, buildout, name, options):
        self.log = logging.getLogger(name)
        self.egg = zc.recipe.egg.Egg(buildout, options['recipe'], options)

        self.buildout, self.name, self.options = buildout, name, options
        options['location'] = os.path.join(
            buildout['buildout']['parts-directory'], name)
        options['bin-directory'] = buildout['buildout']['bin-directory']

        options.setdefault('project', 'project')
        options.setdefault('settings', 'development')

        options.setdefault('urlconf', options['project'] + '.urls')
        options.setdefault(
            'media_root',
            "os.path.join(os.path.dirname(__file__), 'media')")
        # Set this so the rest of the recipe can expect the values to be
        # there. We need to make sure that both pythonpath and extra-paths are
        # set for BBB.
        if 'extra-paths' in options:
            options['pythonpath'] = options['extra-paths']
        else:
            options.setdefault('extra-paths', options.get('pythonpath', ''))

        # Usefull when using archived versions
        buildout['buildout'].setdefault(
            'download-cache',
            os.path.join(buildout['buildout']['directory'],
                         'downloads'))

        # mod_wsgi support script
        options.setdefault('wsgi', 'false')
        options.setdefault('fcgi', 'false')
        options.setdefault('wsgilog', '')
        options.setdefault('logfile', '')

        # only try to download stuff if we aren't asked to install from cache
        self.install_from_cache = self.buildout['buildout'].get(
            'install-from-cache', '').strip() == 'true'

        # Generate the ``site_py_dest`` option
        # http://svn.zope.org/zc.buildout/branches/gary-betafix/SYSTEM_PYTHON_HELP.txt?view=markup
        options['parts-directory'] = os.path.join(
            buildout['options']['parts-directory'], self.name)


    def install(self):
        location = self.options['location']
        base_dir = self.buildout['buildout']['directory']

        project_dir = os.path.join(base_dir, self.options['project'])

        download_dir = self.buildout['buildout']['download-cache']
        if not os.path.exists(download_dir):
            os.mkdir(download_dir)
        if not os.path.exists(self.options['parts-directory']):
            os.mkdir(self.options['parts-directory'])

        version = self.options['version']
        # Remove a pre-existing installation if it is there
        if os.path.exists(location):
            shutil.rmtree(location)

        if self.is_svn_url(version):
            self.install_svn_version(version, download_dir, location,
                                     self.install_from_cache)
        elif self.is_git_install():
            self.install_git_version(version, download_dir, location,
                                     self.install_from_cache)
        else:
            tarball = self.get_release(version, download_dir)
            # Extract and put the dir in its proper place
            self.install_release(version, download_dir, tarball, location)

        extra_paths = [os.path.join(location), base_dir]

        # Add libraries found by a site .pth files to our extra-paths.
        if 'pth-files' in self.options:
            import site
            for pth_file in self.options['pth-files'].splitlines():
                pth_libs = site.addsitedir(pth_file, set())
                if not pth_libs:
                    self.log.warning(
                        "No site *.pth libraries found for pth_file=%s" % (
                         pth_file,))
                else:
                    self.log.info("Adding *.pth libraries=%s" % pth_libs)
                    self.options['extra-paths'] += '\n' + '\n'.join(pth_libs)

        pythonpath = [p.replace('/', os.path.sep) for p in
                      self.options['extra-paths'].splitlines() if p.strip()]

        extra_paths.extend(pythonpath)
        requirements, ws = self.egg.working_set(['djangorecipe'])

        # Create the Django management script
        self.create_manage_script(extra_paths, ws)

        # Create the test runner
        self.create_test_runner(extra_paths, ws)

        # Make the wsgi and fastcgi scripts if enabled
        self.make_scripts(extra_paths, ws)

        # Create default settings if we haven't got a project
        # egg specified, and if it doesn't already exist
        if not self.options.get('projectegg'):
            if not os.path.exists(project_dir):
                self.create_project(project_dir)
            else:
                self.log.info(
                    'Skipping creating of project: %(project)s since '
                    'it exists' % self.options)

        return location

    def install_svn_version(self, version, download_dir, location,
                            install_from_cache):
        svn_url = self.version_to_svn(version)
        download_location = os.path.join(
            download_dir, 'django-' +
            self.version_to_download_suffix(version))
        if not install_from_cache:
            if os.path.exists(download_location):
                if self.svn_update(download_location, version):
                    raise UserError(
                        "Failed to update Django; %s. "
                        "Please check your internet connection." % (
                            download_location))
            else:
                self.log.info("Checking out Django from svn: %s" % svn_url)
                cmd = 'svn co %s %s' % (svn_url, download_location)
                if not self.buildout['buildout'].get('verbosity'):
                    cmd += ' -q'
                if self.command(cmd):
                    raise UserError("Failed to checkout Django. "
                                    "Please check your internet connection.")
        else:
            self.log.info("Installing Django from cache: " + download_location)

        shutil.copytree(download_location, location)

    def is_git_install(self):
        return self.options.has_key("git")

    def install_git_version(self, version, download_dir, location,
                            install_from_cache):
        git_url = self.git_to_url()
        download_location = os.path.join(download_dir, 'django-git')
        archive_location = 'django-git-archive/'
        archive_file = 'django-git.tar'
        if not install_from_cache:
            if os.path.exists(download_location):
                if self.git_update(download_location):
                    raise UserError(
                        "Failed to update Django; %s. "
                        "Please check your internet connection." % (
                            download_location))
            else:
                self.log.info("Checking out Django from git: %s" % git_url)
                cmd = 'git clone --depth 1 %s %s' % (git_url, download_location)
                if not self.buildout['buildout'].get('verbosity'):
                    cmd += ' -q'
                self.log.info("Cloning with: %s" % cmd)
                if self.command(cmd):
                    raise UserError("Failed to clone Django. "
                                    "Please check your internet connection.")
            orig_cwd=os.getcwd()
            os.chdir(download_location)
            cmd = 'git archive --format=tar --prefix=%s --output=%s %s' % (
                archive_location, archive_file, version)
            self.log.info("archiving with: %s" % cmd)
            if self.command(cmd):
                raise UserError("Failed to create Django archive from Git repo.")

            cmd = "tar -xf %s" % archive_file
            if self.command(cmd):
                raise UserError("Unable to unarchive Django archive from Git repo.")
            os.chdir(orig_cwd)
        else:
            self.log.info("Installing Django from previously cloned Git repo: " + download_location)

        shutil.copytree(os.path.join(download_location, archive_location), location)

    def git_to_url(self):
        if self.options['git'] == 'true':
            return 'git://github.com/django/django.git'
        else:
            return self.options['git']

    def git_update(self, location):
        orig_cwd = os.getcwd()
        os.chdir(location)
        cmd = "git pull origin"
        if not self.buildout['buildout'].get('verbosity'):
            cmd += ' -q'
        self.command(cmd)
        os.chdir(orig_cwd)

    def install_release(self, version, download_dir, tarball, destination):
        extraction_dir = os.path.join(download_dir, 'django-archive')
        setuptools.archive_util.unpack_archive(tarball, extraction_dir)
        # Lookup the resulting extraction dir instead of guessing it
        # (Django releases have a tendency not to be consistend here)
        untarred_dir = os.path.join(extraction_dir,
                                    os.listdir(extraction_dir)[0])
        shutil.move(untarred_dir, destination)
        shutil.rmtree(extraction_dir)

    def get_release(self, version, download_dir):
        tarball = os.path.join(download_dir, 'django-%s.tar.gz' % version)

        # Only download when we don't yet have an archive
        if not os.path.exists(tarball):
            download_url = 'http://www.djangoproject.com/download/%s/tarball/'
            self.log.info("Downloading Django from: %s" % (
                    download_url % version))

            tarball_f = open(tarball, 'wb')
            f = urllib2.urlopen(download_url % version)
            tarball_f.write(f.read())
            tarball_f.close()
            f.close()
        return tarball

    def create_manage_script(self, extra_paths, ws):
        project = self.options.get('projectegg', self.options['project'])
        zc.buildout.easy_install.sitepackage_safe_scripts(
            [(self.options.get('control-script', self.name),
              'djangorecipe.manage', 'main')],
            ws, self.options['executable'], self.options['bin-directory'],
            extra_paths=extra_paths,
            site_py_dest=self.options['parts-directory'],
            script_arguments="'%s.%s'" % (project,
                                    self.options['settings']),
        )



    def create_test_runner(self, extra_paths, working_set):
        apps = self.options.get('test', '').split()
        # Only create the testrunner if the user requests it
        if apps:
            zc.buildout.easy_install.sitepackage_safe_scripts(
                [(self.options.get('testrunner', 'test'),
                  'djangorecipe.test', 'main')],
                working_set, self.options['executable'],
                self.options['bin-directory'],
                extra_paths = extra_paths,
                site_py_dest=self.options['parts-directory'],
                script_arguments= "'%s.%s', %s" % (
                    self.options['project'],
                    self.options['settings'],
                    ', '.join(["'%s'" % app for app in apps])),
            )


    def create_project(self, project_dir):
        os.makedirs(project_dir)

        template_vars = {'secret': self.generate_secret()}
        template_vars.update(self.options)

        self.create_file(
            os.path.join(project_dir, 'development.py'),
            development_settings, template_vars)

        self.create_file(
            os.path.join(project_dir, 'production.py'),
            production_settings, template_vars)

        self.create_file(
            os.path.join(project_dir, 'urls.py'),
            urls_template, template_vars)

        self.create_file(
            os.path.join(project_dir, 'settings.py'),
            settings_template, template_vars)

        # Create the media and templates directories for our
        # project
        os.mkdir(os.path.join(project_dir, 'media'))
        os.mkdir(os.path.join(project_dir, 'templates'))

        # Make the settings dir a Python package so that Django
        # can load the settings from it. It will act like the
        # project dir.
        open(os.path.join(project_dir, '__init__.py'), 'w').close()

    def make_scripts(self, extra_paths, ws):
        _script_template = zc.buildout.easy_install.script_template
        for protocol in ('wsgi', 'fcgi'):
            zc.buildout.easy_install.script_template = \
                zc.buildout.easy_install.script_header + \
                    script_template[protocol]
            if self.options.get(protocol, '').lower() == 'true':
                project = self.options.get('projectegg',
                                           self.options['project'])
                zc.buildout.easy_install.sitepackage_safe_scripts(
                    [('%s.%s' % (self.options.get('control-script', self.name),
                                protocol),
                      'djangorecipe.%s' % protocol, 'main')],
                    ws,
                    self.options['executable'], 
                    self.options['bin-directory'],
                    extra_paths = extra_paths,
                    site_py_dest=self.options['parts-directory'],
                    script_arguments= "'%s.%s', logfile='%s'" % (
                        project, self.options['settings'],
                        self.options.get('logfile')),
                )
        zc.buildout.easy_install.script_template = _script_template

    def is_svn_url(self, version):
        # Search if there is http/https/svn or svn+[a tunnel identifier] in the
        # url or if the trunk marker is used, all indicating the use of svn
        svn_version_search = re.compile(
            r'^(http|https|svn|svn\+[a-zA-Z-_]+)://|^(trunk)$').search(version)
        return svn_version_search is not None

    def version_to_svn(self, version):
        if version == 'trunk':
            return 'http://code.djangoproject.com/svn/django/trunk/'
        else:
            return version

    def version_to_download_suffix(self, version):
        if version == 'trunk':
            return 'svn'
        return [p for p in version.split('/') if p][-1]

    def svn_update(self, path, version):
        command = 'svn up'
        revision_search = re.compile(r'@([0-9]*)$').search(
            self.options['version'])

        if revision_search is not None:
            command += ' -r ' + revision_search.group(1)
        self.log.info("Updating Django from svn")
        if not self.buildout['buildout'].get('verbosity'):
            command += ' -q'
        return self.command(command, cwd=path)

    def update(self):
        newest = self.buildout['buildout'].get('newest') != 'false'
        if newest and not self.install_from_cache and \
                self.is_svn_url(self.options['version']):
            self.svn_update(self.options['location'], self.options['version'])

    def command(self, cmd, **kwargs):
        output = subprocess.PIPE
        if self.buildout['buildout'].get('verbosity'):
            output = None
        command = subprocess.Popen(
            cmd, shell=True, stdout=output, **kwargs)
        return command.wait()

    def create_file(self, file, template, options):
        if os.path.exists(file):
            return

        f = open(file, 'w')
        f.write(template % options)
        f.close()

    def generate_secret(self):
        chars = 'abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*(-_=+)'
        return ''.join([random.choice(chars) for i in range(50)])
