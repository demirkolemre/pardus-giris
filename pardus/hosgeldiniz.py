#! /usr/bin/python3
# -*- coding:utf-8 -*-
#
# Copyright 2012-2013 "Korora Project" <dev@kororaproject.org>
# Copyright 2013 "Manjaro Linux" <support@manjaro.org>
# Copyright 2014 Antergos
# Copyright 2015-2016 Martin Wimpress <code@flexion.org>
# Copyright 2015-2016 Luke Horwell <lukehorwell37+code@gmail.com>
#
# Ubuntu MATE Welcome is free software: you can redistribute it and/or modify
# it under the temms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ubuntu MATE Welcome is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ubuntu MATE Welcome. If not, see <http://www.gnu.org/licenses/>.
#

""" Welcome screen for Ubuntu MATE """

import gi
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Notify", "0.7")
gi.require_version("WebKit", "3.0")

import apt
import errno
import gettext
import glob
import inspect
import json
import locale
import logging
import os
import platform
import random
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import urllib.request
import webbrowser

import urllib.error
import urllib.parse
import urllib.request
from aptdaemon.client import AptClient
from aptdaemon.gtk3widgets import AptErrorDialog, AptConfirmDialog, \
                                  AptProgressDialog
import aptdaemon.errors
from aptdaemon.enums import *
from gi.repository import GLib, Gio, GObject, Gdk, Gtk, Notify, WebKit
from ctypes import cdll, byref, create_string_buffer
from threading import Thread
from getpass import getuser

# i18n - if no translation is available use the inline strings
t = gettext.translation("pardusarm hoşgeldiniz", "./locale", fallback=True)
_ = t.gettext


def goodbye(a=None, b=None):
    # NOTE: _a_ and _b_ are passed via the close window 'delete-event'.
    ''' Closing the program '''

    # Refuse to quit if operations are in progress.
    if dynamicapps.operations_busy:
        print('[Welcome] WARNING: Software changes are in progress!')
        title = _("Software Boutique")
        text_busy = _('Software changes are in progress. Please allow them to complete before closing Welcome.')
        ok_label = _("OK")
        messagebox = subprocess.Popen(['zenity',
                                 '--error',
                                 '--title=' + title,
                                 "--text=" + text_busy,
                                 "--ok-label=" + ok_label,
                                 '--window-icon=error',
                                 '--timeout=9'])
        return 1

    else:
        print('[Welcome] Application Closed.')
        Gtk.main_quit()
        # Be quite forceful, particularly those child screenshot windows.
        exit()


def set_proc_title(name=None):
    '''Set the process title'''

    if not name:
        name = os.path.basename(sys.argv[0])

    libc = cdll.LoadLibrary('libc.so.6')
    buff = create_string_buffer(len(name)+1)
    buff.value = name.encode("UTF-8")
    ret = libc.prctl(15, byref(buff), 0, 0, 0)

    if ret != 0:
        print("Failed to set process title")

    return ret


class SimpleApt(object):
    def __init__(self, packages, action):
        self._timeout = 100
        self.packages = packages
        self.action = action
        self.source_to_update = None
        self.update_cache = False
        self.loop = GLib.MainLoop()
        self.client = AptClient()

    def on_error(self, error):
        dynamicapps.operations_busy = False
        if isinstance(error, aptdaemon.errors.NotAuthorizedError):
            # Silently ignore auth failures
            return
        elif not isinstance(error, aptdaemon.errors.TransactionFailed):
            # Catch internal errors of the client
            error = aptdaemon.errors.TransactionFailed(ERROR_UNKNOWN,
                                                       str(error))
        error_dialog = AptErrorDialog(error)
        error_dialog.run()
        error_dialog.hide()

    def on_finished_fix_incomplete_install(self, transaction, status):
        dynamicapps.operations_busy = False
        self.loop.quit()
        if status == 'exit-success':
            Notify.init(_('Fixing incomplete install succeeded'))
            apt_notify=Notify.Notification.new(_('Successfully fixed an incomplete install.'), _('Fixing the incomplete install was successful.'), 'dialog-information')
            apt_notify.show()
            return True
        else:
            Notify.init(_('Fixing incomplete install failed'))
            apt_notify=Notify.Notification.new(_('Failed to fix incomplete install.'), _('Fixing the incomplete install failed.'), 'dialog-error')
            apt_notify.show()
            return False

    def on_finished_fix_broken_depends(self, transaction, status):
        dynamicapps.operations_busy = False
        self.loop.quit()
        if status == 'exit-success':
            Notify.init(_('Fixing broken dependencies succeeded'))
            apt_notify=Notify.Notification.new(_('Successfully fixed broken dependencies.'), _('Fixing the broken dependencies was successful.'), 'dialog-information')
            apt_notify.show()
            return True
        else:
            Notify.init(_('Fixing broken dependencies failed'))
            apt_notify=Notify.Notification.new(_('Failed to fix broken dependencies.'), _('Fixing the broken dependencies failed.'), 'dialog-error')
            apt_notify.show()
            return False

    def on_finished_update(self, transaction, status):
        dynamicapps.operations_busy = False
        # If the action is only to update do not display notifcations
        if self.action == 'update':
            self.loop.quit()
            if status == 'exit-success':
                return True
            else:
                return False
        elif self.action == 'install':
            if status != 'exit-success':
                self.do_notify(status)
                self.loop.quit()
                return False

            GLib.timeout_add(self._timeout,self.do_install)
            return True
        elif self.action == 'upgrade':
            if status != 'exit-success':
                self.do_notify(status)
                self.loop.quit()
                return False

            GLib.timeout_add(self._timeout,self.do_upgrade)
            return True

    def on_finished_install(self, transaction, status):
        dynamicapps.operations_busy = False
        self.loop.quit()
        if status != 'exit-success':
            return False
        else:
            self.do_notify(status)

    def on_finished_remove(self, transaction, status):
        dynamicapps.operations_busy = False
        self.loop.quit()
        if status != 'exit-success':
            return False
        else:
            self.do_notify(status)

    def on_finished_upgrade(self, transaction, status):
        dynamicapps.operations_busy = False
        self.loop.quit()
        if status != 'exit-success':
            return False
        else:
            self.do_notify(status)

    def do_notify(self, status):
        print('Status: ' + status)
        if self.action == 'install':
            title = _('Install')
            noun = _('Installation of ')
            action = _('installed.')
        elif self.action == 'remove':
            title = _('Remove')
            noun = _('Removal of ')
            action = _('removed.')
        elif self.action == 'upgrade':
            title = _('Upgrade')
            noun = _('Upgrade of ')
            action = _('upgraded.')

        # Do not show notifications when updating the cache
        if self.action != 'update':
            if status == 'exit-success':
                Notify.init(title + ' ' + _('complete'))
                apt_notify=Notify.Notification.new(title + ' ' + _('complete'), ', '.join(self.packages) + ' ' + _('has been successfully ') +action, 'dialog-information')
            elif status == 'exit-cancelled':
                Notify.init(title + ' ' + _('cancelled'))
                apt_notify=Notify.Notification.new(title + ' ' + _('cancelled'), noun + ', '.join(self.packages) + ' ' + _('was cancelled.'), 'dialog-information')
            else:
                Notify.init(title + ' ' + _('failed'))
                apt_notify=Notify.Notification.new(title + ' ' + _('failed'), noun + ', '.join(self.packages) + ' ' + _('failed.'), 'dialog-error')

            apt_notify.show()

    def do_fix_incomplete_install(self):
        dynamicapps.operations_busy = True
        # Corresponds to: dpkg --configure -a
        apt_fix_incomplete = self.client.fix_incomplete_install()
        apt_fix_incomplete.connect("finished",self.on_finished_fix_incomplete_install)

        fix_incomplete_dialog = AptProgressDialog(apt_fix_incomplete)
        fix_incomplete_dialog.run(close_on_finished=True, show_error=True,
                reply_handler=lambda: True,
                error_handler=self.on_error,
                )
        return False
        dynamicapps.operations_busy = False

    def do_fix_broken_depends(self):
        dynamicapps.operations_busy = True
        # Corresponds to: apt-get --fix-broken install
        apt_fix_broken = self.client.fix_broken_depends()
        apt_fix_broken.connect("finished",self.on_finished_fix_broken_depends)

        fix_broken_dialog = AptProgressDialog(apt_fix_broken)
        fix_broken_dialog.run(close_on_finished=True, show_error=True,
                reply_handler=lambda: True,
                error_handler=self.on_error,
                )
        return False
        dynamicapps.operations_busy = False

    def do_update(self):
        if self.source_to_update:
            apt_update = self.client.update_cache(self.source_to_update)
        else:
            apt_update = self.client.update_cache()
        apt_update.connect("finished",self.on_finished_update)

        update_dialog = AptProgressDialog(apt_update)
        update_dialog.run(close_on_finished=True, show_error=True,
                reply_handler=lambda: True,
                error_handler=self.on_error,
                )
        return False

    def do_install(self):
        apt_install = self.client.install_packages(self.packages)
        apt_install.connect("finished", self.on_finished_install)

        install_dialog = AptProgressDialog(apt_install)
        install_dialog.run(close_on_finished=True, show_error=True,
                        reply_handler=lambda: True,
                        error_handler=self.on_error,
                        )
        return False

    def do_remove(self):
        apt_remove = self.client.remove_packages(self.packages)
        apt_remove.connect("finished", self.on_finished_remove)

        remove_dialog = AptProgressDialog(apt_remove)
        remove_dialog.run(close_on_finished=True, show_error=True,
                        reply_handler=lambda: True,
                        error_handler=self.on_error,
                        )
        return False

    def do_upgrade(self):
        apt_upgrade = self.client.upgrade_system(True)
        apt_upgrade.connect("finished", self.on_finished_upgrade)

        upgrade_dialog = AptProgressDialog(apt_upgrade)
        upgrade_dialog.run(close_on_finished=True, show_error=True,
                        reply_handler=lambda: True,
                        error_handler=self.on_error,
                        )
        return False

    def install_packages(self):
        dynamicapps.operations_busy = True
        if self.update_cache:
            GLib.timeout_add(self._timeout,self.do_update)
        else:
            GLib.timeout_add(self._timeout,self.do_install)
        self.loop.run()
        dynamicapps.operations_busy = False

    def remove_packages(self):
        dynamicapps.operations_busy = True
        GLib.timeout_add(self._timeout,self.do_remove)
        self.loop.run()
        dynamicapps.operations_busy = False

    def upgrade_packages(self):
        dynamicapps.operations_busy = True
        if self.update_cache:
            GLib.timeout_add(self._timeout,self.do_update)
        else:
            GLib.timeout_add(self._timeout,self.do_upgrade)
        self.loop.run()
        dynamicapps.operations_busy = False

    def fix_incomplete_install(self):
        dynamicapps.operations_busy = True
        GLib.timeout_add(self._timeout,self.do_fix_incomplete_install)
        self.loop.run()
        dynamicapps.operations_busy = False

    def fix_broken_depends(self):
        dynamicapps.operations_busy = True
        GLib.timeout_add(self._timeout,self.do_fix_broken_depends)
        self.loop.run()
        dynamicapps.operations_busy = False

def update_repos():
    transaction = SimpleApt('', 'update')
    transaction.update_cache = True
    transaction.do_update()

def fix_incomplete_install():
    transaction = SimpleApt('', 'fix-incomplete-install')
    transaction.fix_incomplete_install()

def fix_broken_depends():
    transaction = SimpleApt('', 'fix-broken-depends')
    transaction.fix_broken_depends()

def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc: # Python >2.5
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise

def get_aacs_db():
    home_dir = GLib.get_home_dir()
    key_url = 'http://www.labdv.com/aacs/KEYDB.cfg'
    key_db = os.path.join(home_dir, '.config', 'aacs', 'KEYDB.cfg')
    mkdir_p(os.path.join(home_dir, '.config', 'aacs'))
    print('[AACS] Getting ' + key_url + ' and saving as ' + key_db)

    # Download the file from `key_url` and save it locally under `file_name`:
    try:
        with urllib.request.urlopen(key_url) as response, open(key_db, 'wb') as out_file:
            data = response.read() # a `bytes` object
            out_file.write(data)

        Notify.init(_('Blu-ray AACS database install succeeded'))
        aacs_notify=Notify.Notification.new(_('Successfully installed the Blu-ray AACS database.'), _('Installation of the Blu-ray AACS database was successful.'), 'dialog-information')
        aacs_notify.show()
    except:
        Notify.init(_('Blu-ray AACS database install failed'))
        aacs_notify=Notify.Notification.new(_('Failed to install the Blu-ray AACS database.'), _('Installation of the Blu-ray AACS database failed.'), 'dialog-error')
        aacs_notify.show()

class PreInstallation(object):
    #
    #   See the JSON Structure in the `DynamicApps` class on
    #    how to specify pre-configuration actions in `applications.json`
    #

    def __init__(self):
        # Always ensure we have the correct variables.
        self.os_version = "v3.17"
        self.codename = platform.dist()[2]
        arg.print_verbose("Pre-Install", "System is running Ubuntu " + self.os_version + " (" + self.codename + ")")

    def process_packages(self, program_id, action):
        simulating = arg.simulate_software_changes
        print(' ')

        # Get category for this program, which can be used to retrieve data later.
        category = dynamicapps.get_attribute_for_app(program_id, 'category')

        try:
            preconfig = dynamicapps.index[category][program_id]['pre-install']
        except:
            print('[Pre-Install] Missing pre-configuration data for "' + program_id + '". Refusing to continue.')
            return

        try:
            if action == 'install':
                packages = dynamicapps.index[category][program_id]['install-packages']
                print('[Apps] Packages to be installed:\n               ' + packages)
            elif action == 'remove':
                packages = dynamicapps.index[category][program_id]['remove-packages']
                print('[Apps] Packages to be removed:\n               ' + packages)
            elif action == 'upgrade':
                packages = dynamicapps.index[category][program_id]['upgrade-packages']
                print('[Apps] Packages to be upgraded:\n               ' + packages)
            else:
                print('[Apps] ERROR: Invalid action was requested.')
                return
        except:
            print('[Apps] ERROR: No packages retrieved for requested action.')
            return

        # Validate that we have packages to work with.
        if len(packages):
            packages = packages.split(',')
        else:
            print('[Apps] ERROR: No package(s) supplied for "' + program_id + '".')
            return
        transaction = SimpleApt(packages, action)

        # Function to run privileged commands.
        def run_task(function):
            subprocess.call(['pkexec', '/usr/lib/ubuntu-mate/ubuntu-mate-welcome-repository-installer', os.path.abspath(os.path.join(app._data_path, 'js/applications.json')), function, category, program_id, target])

        # Determine if any pre-configuration is specific to a codename.
        try:
            preinstall = dynamicapps.index[category][program_id]['pre-install']
            codenames = list(preinstall.keys())
        except:
            print('[Pre-Install] No pre-install data specified for "' + program_id + '". This application entry is invalid.')
            return
        arg.print_verbose('Pre-Install','Available configurations: ' + str(codenames))
        target = None
        for name in codenames:
            if name == self.codename:
                target = name
                break
        if not target:
                target = 'all'
                arg.print_verbose('Pre-Install','Using "all" pre-configuration.')
        else:
            arg.print_verbose('Pre-Install','Using configuration for: "' + target + '".')

        methods = preinstall[target]['method'].split('+')
        if not methods:
            print('[Pre-Install] No pre-install method was specified. The index is invalid.')
        else:
            arg.print_verbose('Pre-Install','Configuration changes: ' + str(methods))

        # Perform any pre-configuration, if necessary.
        if action == 'install' or action == 'upgrade':
            for method in methods:
                if method == 'skip':
                    arg.print_verbose('Pre-Install','No need! The package is already in the archives.')
                    continue

                elif method == 'partner-repo':
                    print('[Pre-Install] Enabling the Ubuntu partner repository.')
                    if not simulating:
                        run_task('enable_partner_repository')
                        transaction.update_cache = True

                elif method == 'ppa':
                    try:
                        ppa = preinstall[target]['enable-ppa']
                    except:
                        print('[Pre-Install] Missing "enable-ppa" attribute. Cannot add PPA as requested.')
                        return
                    print('[Pre-Install] Adding PPA: "' + ppa + '" and updating cache.')
                    if not simulating:
                        run_task('enable_ppa')
                        transaction.update_cache = True
                    try:
                        source_file = preinstall[target]['source-file'].replace('OSVERSION',self.os_version).replace('CODENAME',self.codename)
                        print('[Pre-Install] Updating Apt Source: "' + source_file + '.list"')
                        if not simulating:
                            transaction.source_to_update = source_file + '.list'
                    except:
                        arg.print_verbose('Pre-Install','Updating entire cache as no source file was specified.')

                elif method == 'manual':
                    # Do we get the apt key from a URL?
                    try:
                        apt_key_url = preinstall[target]['apt-key-url']
                        print('[Pre-Install] Getting Apt key from URL: "' + apt_key_url + '"')
                        if not simulating:
                            run_task('add_apt_key_from_url')
                    except:
                        arg.print_verbose('Pre-Install', 'No apt key to retrieve from a URL.')

                    # Do we get the apt key from the server?
                    try:
                        apt_key_server = preinstall[target]['apt-key-server'][0]
                        apt_key_key =    preinstall[target]['apt-key-server'][1]
                        print('[Pre-Install] Getting key "' + apt_key_key + '" from keyserver: "' + apt_key_server + '"')
                        if not simulating:
                            run_task('add_apt_key_from_keyserver')
                    except:
                        arg.print_verbose('Pre-Install', 'No apt key to retrieve from a key server.')

                    # Do we need to add an apt source file?
                    try:
                        source = preinstall[target]['apt-sources']
                        source_file = preinstall[target]['source-file'].replace('OSVERSION',self.os_version).replace('CODENAME',self.codename)
                        print('[Pre-Install] Writing source file: ' + source_file + '.list')
                        print('              -------- Start of file ------')
                        for line in source:
                            print('              ' + line.replace('OSVERSION',self.os_version).replace('CODENAME',self.codename))
                        print('              -------- End of file ------')
                        try:
                            print('[Pre-Install] Updating Apt Source: ' + source_file + '.list')
                            if not simulating:
                                run_task('add_apt_sources')
                                transaction.source_to_update = source_file + '.list'
                                transaction.update_cache = True
                        except:
                            arg.print_verbose('Pre-Install','Failed to add apt sources!')
                    except:
                        arg.print_verbose('Pre-Install','No source data or source file to write.')

        elif action == 'remove':
            try:
                # The function uses wild cards, so we don't need to worry about being explict.
                listname = preinstall[target]['source-file'].replace('CODENAME','').replace('OSVERSION','')
                if simulating:
                    print('[Simulation] Deleting Apt Source: ' + listname)
                else:
                    run_task('del_apt_sources')
            except:
                print('[Pre-Install]', 'No apt source specified, so none will be removed.')


        # Pre-configuration complete. Now perform the operations, unless this was just a simulation.
        if simulating:
            print('[Pre-Install] Simulation flag active. No changes will be performed.')
            return
        else:
            if transaction.action == 'install':
                transaction.install_packages()
            elif transaction.action == 'remove':
                transaction.remove_packages()
            elif transaction.action == 'upgrade':
                transaction.upgrade_packages()


class WelcomeConfig(object):
    """ Manages Welcome configuration """
    def __init__(self):
        # store our base architecture
        self.os_version = "v3.17"
        self.os_codename = platform.dist()[2]
        self.os_title = 'pardus arm ' + self.os_version
        self._arch = systemstate.arch

        # store full path to our binary
        self._welcome_bin_path = os.path.abspath(inspect.getfile(inspect.currentframe()))

        # directory for the configuration
        self._config_dir = os.path.expanduser('~/.config/ubuntu-mate/welcome/')

        # autostart directory
        self._autostart_dir = os.path.expanduser('~/.config/autostart/')

        # full path to the autostart symlink
        self._autostart_path = os.path.expanduser(os.path.join(self._autostart_dir, 'ubuntu-mate-welcome.desktop'))

        # ensure our config and autostart directories exists
        for _dir in [self._config_dir, self._autostart_dir]:
            if not os.path.exists(_dir):
                try:
                    os.makedirs(_dir)
                except OSError as err:
                    print(err)
                    pass

        # does autostart symlink exist
        self._autostart = os.path.exists(self._autostart_path)

    @property
    def autostart(self):
        return self._autostart

    @autostart.setter
    def autostart(self, state):
        if state and not os.path.exists(self._autostart_path):
            # create the autostart symlink
            try:
                os.symlink('/usr/share/applications/ubuntu-mate-welcome.desktop', self._autostart_path)
            except OSError as err:
                print(err)
                pass
        elif not state and os.path.exists(self._autostart_path):
            # remove the autostart symlink
            try:
                os.unlink(self._autostart_path)
            except OSError as err:
                print(err)
                pass

        # determine autostart state based on absence of the disable file
        self._autostart = os.path.exists(self._autostart_path)


class AppView(WebKit.WebView):
    def __init__(self, slide_list = None):
        """
            Args:
            slide_list : A list of tuples containing the filenames of translated html files
                         or, if no translation is available, the filename of the original
                         untranslated slide
        """
        WebKit.WebView.__init__(self)
        WebKit.WebView.__init__(self)
        self._config = WelcomeConfig()
        self._apt_cache = apt.Cache()
        self.connect('load-finished', self._load_finished_cb)
        self.connect('navigation-policy-decision-requested', self._nav_request_policy_decision_cb)
        self.l_uri = None
        self._slide_list = slide_list

        self.set_zoom_level(systemstate.zoom_level)
        print('[Welcome] Setting zoom level to: ' + str(systemstate.zoom_level))

        # Disable right-click context menu as it isn't needed.
        self.props.settings.props.enable_default_context_menu = False

        # Perform a smooth transition for footer icons.
        self.do_smooth_footer = False

    def _push_config(self):
        ### Global - On all pages ###
        self.execute_script("$('#os_title').html('%s')" % self._config.os_title)
        self.execute_script("$('#os_version').html('%s')" % self._config.os_version)
        self.execute_script("$('#autostart').toggleClass('fa-check-square', %s).toggleClass('fa-square', %s)" % (json.dumps(self._config.autostart), json.dumps(not self._config.autostart)))

        # If this is a Live session (booted from ISO) show the
        # 'Install OS' button, if running on an installed system show
        # the 'Install Software' button.
        if systemstate.session_type == 'live':
            self.execute_script("$('#install').show();")
            self.execute_script("$('#software').hide();")
            self.execute_script("$('.live-session').hide();")
            self.execute_script("$('.live-session-only').show();")
        else:
            self.execute_script("$('#install').hide();")
            self.execute_script("$('#software').show();")
            self.execute_script("$('.live-session').show();")
            self.execute_script("$('.live-session-only').hide();")

        # If started from a Raspberry Pi.
        if systemstate.session_type == 'pi':
            self.execute_script("$('.rpi-only').show();")
        else:
            self.execute_script("$('.rpi-only').hide();")

        # Display warnings if the user is not connected to the internet.
        if systemstate.is_online:
            self.execute_script("$('.offline').hide();")
            self.execute_script("$('.online').show();")
        else:
            self.execute_script("$('.offline').show();")
            self.execute_script("$('.online').hide();")

        ## Social Links ##
        footer_left = '<div id="social" class="pull-left"> \
        <a href="cmd://link?https://plus.google.com/communities/108331279007926658904" title="Google+"><img src="img/social/google+.svg"></a> \
        <a href="cmd://link?https://www.facebook.com/UbuntuMATEedition/" title="Facebook"><img src="img/social/facebook.svg"></a> \
        <a href="cmd://link?https://twitter.com/ubuntu_mate" title="Twitter"><img src="img/social/twitter.svg"></a> \
        <a href="cmd://link?https://ubuntu-mate.org" title="ubuntu-mate.org"><img src="img/humanity/website.svg"></a> \
        <a href="cmd://link?https://ubuntu-mate.org/donate/" title="ubuntu-mate.org/donate/"><img src="img/humanity/donate.svg"></a> \
        </div>'

        ## Boutique Footer ##
        str_subscribed = _("Set to retrieve the latest software listings.")
        str_subscribe_link = _("Retrieve the latest software listings.")
        str_subscribing = _("Please wait while the application is being updated...")
        str_listing_version = _("Version:")

        boutique_footer = '<div id="boutique-footer" class="pull-left"> \
        <p hidden id="update-subscribed"><span class="fa fa-check"></span> ' + str_subscribed + '</p> \
        <p hidden id="update-notification"><a href="cmd://subscribe-updates"><span class="fa fa-exclamation-circle"></span> ' + str_subscribe_link + '</a></p> \
        <p hidden id="update-subscribing"><img src="img/welcome/processing-dark.gif" width="16px" height="16px"> ' + str_subscribing + '</p> \
        <p><b>' + str_listing_version + '</b> <span id="boutique-version"></span></p> \
        </div>'

        # Do not show footer links on splash or software page.
        if not arg.jump_software_page:
            if not self.current_page == 'splash.html' and not self.current_page == 'software.html':
                self.execute_script("$('#footer-global-left').html('" + footer_left + "');")

        # Show the button depending on context.
        footer_close = '<a href="cmd://quit" class="btn btn-inverse">' + _("Close") + '&zwnj;</a>'
        footer_skip  = '<a onclick="continueToPage(true)" class="btn btn-inverse">' + _("Skip") + '</a>'

        if self.current_page == 'splash.html':
            self.execute_script("$('#footer-global-right').html('" + footer_skip + "');")
        elif arg.jump_software_page:
            # Do not show a "Close" button for the Boutique.
            pass
        else:
            self.execute_script("$('#footer-global-right').html('" + footer_close + "');")

        # Smoothly fade in the footer links between pages.
        #   splash → index
        #   index ← → software
        if self.do_smooth_footer or self.current_page == 'software.html':
            self.do_smooth_footer = False
            self.execute_script("$('#footer-left').hide();")
            self.execute_script("$('#footer-left').fadeIn();")

        # Individual Page Actions
        ### Main Menu ###
        if self.current_page == 'index.html':
            if systemstate.session_type == 'guest':
                # Disable features that are unavailable to guests.
                self.execute_script("$('#gettingstarted').hide();")
                self.execute_script("$('#software').hide();")
                self.execute_script("$('#introduction').addClass('btn-success');")
                self.execute_script("$('#community').addClass('btn-success');")

            # Check whether the system is subscribed for receiving more up-to-date versions of Welcome.
            self.execute_script('$("#update-subscribing").hide()')
            if not systemstate.updates_subscribed:
                if systemstate.is_online:
                    self.execute_script('$("#update-notification").fadeIn("slow")')
            else:
                self.execute_script('$("#update-notification").hide()')

            # Disable confetti on machines that may suffer performance issues.
            if systemstate.arch == 'armhf':
                self.execute_script('var disable_confetti = true;')
            elif systemstate.arch == 'powerpc':
                self.execute_script('var disable_confetti = true;')
            else:
                self.execute_script('var disable_confetti = false;')

            # Special event strings.
            self.execute_script('var days_in = "&zwnj;in&zwnj;"')
            self.execute_script('var future_days = "&zwnj;days.&zwnj;"')
            self.execute_script('var days_ago = "&zwnj;days ago.&zwnj;"')
            self.execute_script('var yesterday = "&zwnj;yesterday.&zwnj;"')
            self.execute_script('var tomorrow = "&zwnj;tomorrow.&zwnj;"')
            self.execute_script('var years_ago = "&zwnj;years ago today.&zwnj;"')
            self.execute_script('var today_string = "&zwnj;today.&zwnj;"')
            self.execute_script('var years_old = "&zwnj;years old&zwnj;"')

            self.execute_script('var flavour_anniversary_future  = "&zwnj;Ubuntu MATE\'s official flavour anniversary&zwnj;"')
            self.execute_script('var flavour_anniversary_present = "&zwnj;Ubuntu MATE become an official flavour&zwnj;"')
            self.execute_script('var flavour_anniversary_past    = "&zwnj;Ubuntu MATE\'s official flavour anniversary was&zwnj;"')

            self.execute_script('var project_birthday_future  = "&zwnj;Ubuntu MATE will be&zwnj;"')
            self.execute_script('var project_birthday_present = "&zwnj;Ubuntu MATE is&zwnj;"')
            self.execute_script('var project_birthday_past    = "&zwnj;Ubuntu MATE turned&zwnj;"')

            self.execute_script('var project_birthday         = "&zwnj;Happy Birthday!&zwnj;"')
            self.execute_script('var celebrate_new_year       = "&zwnj;Happy New Year from Ubuntu MATE!&zwnj;"')

            self.execute_script('var project_release_future   = "&zwnj;will be released&zwnj;"')
            self.execute_script('var project_release_present  = "&zwnj;is released today!&zwnj;"')
            self.execute_script('var project_release_past     = "&zwnj;was released&zwnj;"')
            self.execute_script('var project_release_thanks   = "&zwnj;Thank you for testing&zwnj;"')

            self.execute_script('checkDates();')

        ### Splash ###
        if self.current_page == 'splash.html':
            self.do_smooth_footer = True
            # Determine which screen to show after the splash screen.
            if systemstate.session_type == 'live':
                self.execute_script('var splashNextPage = "hellolive"')
            elif systemstate.session_type == 'guest':
                self.execute_script('var splashNextPage = "helloguest"')
            else:
                self.execute_script('var splashNextPage = "index"')

            # Smoothly fade footer when entering main menu.
            self.splash_finished = True

        ### Chat Page ###
        if self.current_page == 'chatroom.html':
            if self._apt_cache['hexchat'].is_installed:
                self.execute_script("$('.hexchat').show();")
                self.execute_script("$('.webchat').hide();")
            else:
                self.execute_script("$('.hexchat').hide();")
                self.execute_script("$('.webchat').show();")

        ### Getting Started Page ###
        if self.current_page == 'gettingstarted.html':
            # Display information tailored to graphics vendor (Getting Started / Drivers)
            self.execute_script('var graphicsVendor = "' + systemstate.graphics_vendor + '";')
            self.execute_script('var graphicsGrep = "' + systemstate.graphics_grep + '";')
            self.execute_script('$("#boot-mode").html("' + systemstate.boot_mode + '")')

            # Update any applications featured on these pages.
            dynamicapps.update_app_status(self, 'hardinfo')
            dynamicapps.update_app_status(self, 'gparted')
            dynamicapps.update_app_status(self, 'gnome-disk-utility')
            dynamicapps.update_app_status(self, 'mate-disk-usage-analyzer')
            dynamicapps.update_app_status(self, 'mate-system-monitor')
            dynamicapps.update_app_status(self, 'psensor')
            dynamicapps.update_app_status(self, 'boot-repair')
            dynamicapps.update_app_status(self, 'codecs')
            dynamicapps.update_app_status(self, 'firmware')
            dynamicapps.update_app_status(self, 'hp-printer')
            dynamicapps.update_app_status(self, 'keyboard-chinese')
            dynamicapps.update_app_status(self, 'keyboard-japanese')
            dynamicapps.update_app_status(self, 'keyboard-korean')

        ### Software Page ###
        if self.current_page == 'software.html':
            dynamicapps.hide_non_free = False
            self.do_smooth_footer = True

            # If loading a minimal "Get More Software" only page.
            if arg.jump_software_page:
                self.execute_script('$("#menu-button").hide()')
                self.execute_script('$("#navigation-title").html("<span id=\'navigation-sub-title\'>Curated software collection</span>")')
                self.execute_script('$("#navigation-sub-title").css("color","#DED9CB")')

            # Pass 'Servers' variable used for one-click server links.
            self.execute_script('var server_string = "' + _("Servers") + '"')

            # Dynamically load application lists.
            dynamicapps.populate_categories(self)
            dynamicapps.update_all_app_status(self)
            dynamicapps.populate_featured_apps(self)

            # Show a different footer in the Boutique.
            self.execute_script("$('#footer-global-left').html('" + boutique_footer + "');")

            # Set version and subscription details.
            self.execute_script('$("#boutique-version").html("' + systemstate.welcome_version + '")')
            if systemstate.updates_subscribed:
                self.execute_script('$("#update-subscribed").show()')
            else:
                self.execute_script('$("#update-notification").show()')

        ### Raspberry Pi Page ###
        if self.current_page == 'rpi.html':
            # Check file system resize flag.
            systemstate.rpi_resize('check', self)

        ### Donate ###
        if self.current_page == 'donate.html':
            # Pass translatable short-hand month strings for the supporters grid.
            self.execute_script('short_jan = "' + _("Jan") + '"')
            self.execute_script('short_feb = "' + _("Feb") + '"')
            self.execute_script('short_mar = "' + _("Mar") + '"')
            self.execute_script('short_apr = "' + _("Apr") + '"')
            self.execute_script('short_may = "' + _("May") + '"')
            self.execute_script('short_jun = "' + _("Jun") + '"')
            self.execute_script('short_jul = "' + _("Jul") + '"')
            self.execute_script('short_aug = "' + _("Aug") + '"')
            self.execute_script('short_sep = "' + _("Sep") + '"')
            self.execute_script('short_oct = "' + _("Oct") + '"')
            self.execute_script('short_nov = "' + _("Nov") + '"')
            self.execute_script('short_dec = "' + _("Dec") + '"')

    def _load_finished_cb(self, view, frame):
        self._push_config()

    def _nav_request_policy_decision_cb(self, view, frame, net_req, nav_act, pol_dec):
        uri = net_req.get_uri()
        self.current_page = uri.rsplit('/', 1)[1]

        try:
            if uri.index('#') > 0:
                uri = uri[:uri.index('#')]
        except ValueError:
            pass

        if uri == self.l_uri:
            pol_dec.use()
            return True

        if uri.startswith('cmd://'):
            self._do_command(uri)
            return True

        self.l_uri = uri

        if self._slide_list is None:
            # no translations have to been specified, so we can just load the specified page..
            page = urllib.request.urlopen(uri)
        else:
            # find the slide in slide_list
            head, slide_name = os.path.split(uri)
            found = False
            for slide in self._slide_list:
                head, trans_slide_name = os.path.split(slide)
                if slide_name == trans_slide_name:
                    found = True
                    # load the translated html
                    trans_uri = urllib.parse.urljoin('file:', urllib.request.pathname2url(slide))
                    page = urllib.request.urlopen(trans_uri)
                    break

            if not found:
                # this should never happen, but if it does, recover by loading the originally specified page
                arg.print_verbose('Translation','Couldn''t find slide %s when getting translation' %uri)
                page = urllib.request.urlopen(uri)

        # use UTF-8 encoding as fix for &nbsp chars in translated html
        #
        # When loading the html, for the base_uri, use the uri of the originally specified
        # page (which will be in _data_path) rather than the uri of any translated html we may be using instead.
        # Doing this allows the js, css, fonts etc. directories to be located by the translated page,
        frame.load_string(page.read().decode(), "text/html", "UTF-8", uri);

        pol_dec.ignore()
        return True

    def _do_command(self, uri):
      if uri.startswith('cmd://'):
          uri = uri[6:]

      try:
        if uri.startswith('install-appid?'):
            dynamicapps.modify_app(self, 'install', uri[14:])
        elif uri.startswith('remove-appid?'):
            dynamicapps.modify_app(self, 'remove', uri[13:])
        elif uri.startswith('upgrade-appid?'):
            dynamicapps.modify_app(self, 'upgrade', uri[14:])
        elif uri.startswith('launch-appid?'):
            dynamicapps.launch_app(uri[13:])
        elif uri.startswith('filter-apps?'):
            filter_name = uri.split('?')[1]
            nonfree_toggle = uri.split('?')[2]
            if nonfree_toggle == 'toggle':
                dynamicapps.apply_filter(self, filter_name, True)
            else:
                dynamicapps.apply_filter(self, filter_name)
        elif uri.startswith('app-info-show?'):
            appid = uri.split('?')[1]
            self.execute_script('$("#info-show-' + appid + '").hide()')
            self.execute_script('$("#info-hide-' + appid + '").show()')
            self.execute_script('$("#details-' + appid + '").fadeIn("fast")')
        elif uri.startswith('app-info-hide?'):
            appid = uri.split('?')[1]
            self.execute_script('$("#info-show-' + appid + '").show()')
            self.execute_script('$("#info-hide-' + appid + '").hide()')
            self.execute_script('$("#details-' + appid + '").fadeOut("fast")')
        elif uri.startswith('screenshot?'):
            filename = uri.split('?')[1]
            dynamicapps.show_screenshot(filename)
        elif uri == 'apt-update':
            update_repos()
            self._apt_cache.close()
            self._apt_cache = apt.Cache()
            self._push_config()
        elif uri == 'fix-incomplete-install':
            fix_incomplete_install()
            self._apt_cache.close()
            self._apt_cache = apt.Cache()
            self._push_config()
        elif uri == 'fix-broken-depends':
            fix_broken_depends()
            self._apt_cache.close()
            self._apt_cache = apt.Cache()
            self._push_config()
        elif uri == 'get-aacs-db':
            self.execute_script('$(".bluray-applying").show()')
            get_aacs_db()
            self.execute_script('$(".bluray-applying").hide()')
        elif uri == 'autostart':
            self._config.autostart ^= True
            self._push_config()
        elif uri == 'install':
            subprocess.Popen(['ubiquity','gtk_ui'])
        elif uri == 'backup':
            subprocess.Popen(['deja-dup-preferences'])
        elif uri == 'chatroom':
            subprocess.Popen(['hexchat','IRC://irc.freenode.net/ubuntu-mate'])
        elif uri == 'control':
            subprocess.Popen(['mate-control-center'])
        elif uri == 'drivers':
            subprocess.Popen(['software-properties-gtk','--open-tab=4'])
        elif uri == 'firewall':
            subprocess.Popen(['gufw'])
        elif uri == 'language':
            subprocess.Popen(['gnome-language-selector'])
        elif uri == 'users':
            subprocess.Popen(['users-admin'])
        elif uri == 'quit':
            goodbye()
        elif uri == 'tweak':
            subprocess.Popen(['mate-tweak'])
        elif uri == 'update':
            subprocess.Popen(['update-manager'])
        elif uri == 'printers':
            subprocess.Popen(['system-config-printer'])
        elif uri == 'gparted':
            subprocess.Popen(['gparted-pkexec'])
        elif uri == 'sysmonitor':
            subprocess.Popen(['mate-system-monitor'])
        elif uri.startswith('run?'):
            subprocess.Popen([uri[4:]])
        elif uri.startswith('link?'):
            webbrowser.open_new_tab(uri[5:])
        elif uri == 'checkInternetConnection':
            systemstate.check_internet_connection()
            if systemstate.is_online:
                self.execute_script("$('.offline').hide();")
                self.execute_script("$('.online').show();")
            else:
                self.execute_script("$('.offline').show();")
                self.execute_script("$('.online').hide();")
        elif uri == 'resize-rpi':
            systemstate.rpi_resize('do-resize', self)
        elif uri == 'reboot-rpi':
            systemstate.rpi_resize('reboot')
        elif uri == 'subscribe-updates':
            print('[Welcome] Subscribing to Ubuntu MATE Welcome Updates...')
            self.execute_script("$('#update-notification').hide()")
            self.execute_script("$('#update-subscribing').show()")
            dynamicapps.modify_app(self, 'install', 'ubuntu-mate-welcome')
            # Verify if the PPA was successfully added.
            if os.path.exists(systemstate.welcome_ppa_file):
                if os.path.getsize(systemstate.welcome_ppa_file) > 0:
                    print('[Welcome] Success, PPA added! Application restarting...')
                    os.execv(__file__, sys.argv)
            else:
                print('[Welcome] Failed, PPA not detected!')
                self.execute_script('$("#update-subscribing").hide()')
                self.execute_script('$("#update-notification").show()')
        elif uri == 'init-system-info':
            systemstate.get_system_info(self)
        else:
            print('[Error] Unknown command: ', uri)
      except Exception as e:
        print('[Error] Failed to execute command: ', uri)
        print('[Error] Exception: ', e)


class WelcomeApp(object):
    def __init__(self):
        # establish our location
        self._location = os.path.dirname( os.path.abspath(inspect.getfile(inspect.currentframe())) )

        # check for relative path
        if( os.path.exists( os.path.join(self._location, 'data/' ) ) ):
            print('[Debug] Using relative path for data source. Non-production testing.')
            self._data_path = os.path.join(self._location, 'data/')
        elif( os.path.exists('/pardus') ):
            print('Using /pardus path.')
            self._data_path = '/pardus'
        else:
            print('Unable to source the ubuntu-mate-welcome data directory.')
            sys.exit(1)

        self._build_app()

    def _get_translated_slides(self):
        """ If a locale has been specified on the command line, get translated slides
            for that. If not, get translated slides for the current locale

        Do not assume that every slide has a translation, check each file individually

        Returns:
            a list of filenames of translated slides - if there is no translated version
            of a slide, the filename of the untranslated version from the _data_path directory
            is used instead

        """

        if (arg.locale is not None):
            locale_to_use = arg.locale
        else:
            locale_to_use = str(locale.getlocale()[0])

        def set_locale_dir(this_locale):
            # check for relative path
            if (os.path.exists(os.path.join(self._location, 'i18n', this_locale))):
                locale_dir = os.path.join(self._location, 'i18n', this_locale)
                print('[i18n] Using ' + this_locale + '. Non-production testing.')
            elif (os.path.exists(os.path.join('/pardus', this_locale))):
                locale_dir = os.path.join('/pardus', this_locale)
                print('[i18n] Using ' + this_locale)
            else:
                locale_dir = ''
                print('[i18n] Locale ' + this_locale + ' not available.')

            return locale_dir

        # if no locale exists, try a generic locale.
        locale_dir = set_locale_dir(locale_to_use)
        if locale_dir == '':
            locale_generic = locale_to_use.split('_')[0]
            print('[i18n] Trying ' + locale_generic + '...')
            locale_dir = set_locale_dir(locale_generic)

        results = []

        slides = glob.glob(os.path.join(self._data_path, '*.html'))
        for slide in slides:
            # get the slide name and see if a translated version exists
            head, slide_name = os.path.split(slide)

            trans_slide = os.path.join(locale_dir, slide_name)
            if os.path.exists(trans_slide):
                results.append(trans_slide)
                arg.print_verbose("i18n","Will use %s translation of %s" %(locale_to_use, slide_name))
            else:
                results.append(slide)
                arg.print_verbose("i18n","No %s translation of %s found. Will use version in _data_path" %(locale_to_use, slide))

        return results

    def _build_app(self):

        # Slightly different attributes if "--software-only" is activated.
        if arg.jump_software_page:
            title = _("Software Boutique")
            width = 900
            height = 600
            load_file = 'software-only.html'
        else:
            title = _("Hoşgeldiniz")
            width = 800
            height = 552
            load_file = 'splash.html'

        # Enlarge the window should the text be any larger.
        if systemstate.zoom_level == 1.1:
            width = width + 20
            height = height + 20
        elif systemstate.zoom_level == 1.2:
            width = width + 60
            height = height + 40
        elif systemstate.zoom_level == 1.3:
            width = width + 100
            height = height + 60
        elif systemstate.zoom_level == 1.4:
            width = width + 130
            height = height + 100
        elif systemstate.zoom_level == 1.5:
            width = width + 160
            height = height + 120

        # Jump to a specific page for testing purposes.
        if arg.jump_to:
            load_file = arg.jump_to + '.html'

        # build window
        w = Gtk.Window()
        w.set_position(Gtk.WindowPosition.CENTER)
        w.set_wmclass('pardus Welcome', 'pardus Welcome')
        w.set_title(title)

        # http://askubuntu.com/questions/153549/how-to-detect-a-computers-physical-screen-size-in-gtk
        s = Gdk.Screen.get_default()
        if s.get_height() <= 600:
            w.set_size_request(768, 528)
        else:
            w.set_size_request(width, height)

        icon_dir = os.path.join(self._data_path, 'img', 'welcome', 'pardusarm.png')
        w.set_icon_from_file(icon_dir)

        #get the translated slide list
        trans_slides = self._get_translated_slides()
        # build webkit container
        mv = AppView(trans_slides)

        # load our index file
        file = os.path.abspath(os.path.join(self._data_path, load_file))

        uri = 'file://' + urllib.request.pathname2url(file)
        mv.open(uri)

        # build scrolled window widget and add our appview container
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add(mv)

        # build an autoexpanding box and add our scrolled window
        b = Gtk.VBox(homogeneous=False, spacing=0)
        b.pack_start(sw, expand=True, fill=True, padding=0)

        # add the box to the parent window and show
        w.add(b)
        w.connect('delete-event', goodbye)
        w.show_all()

        self._window = w
        self._appView = mv

    def run(self):
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        Gtk.main()

    def close(self, p1, p2):
        Gtk.main_quit(p1, p2);


class SystemState(object):
    def __init__(self):
        # Set initial variables
        self.is_online = False
        self.user_name = getuser()
        self.updates_subscribed = False
        self.welcome_version = 'Unknown'
        self.rpi_resize_pending = False

        # Get current architecture of system.
        # Outputs 'i386', 'amd64', etc - Based on packages instead of kernel (eg. i686, x86_64).
        self.arch = str(subprocess.Popen(['dpkg','--print-architecture'], stdout=subprocess.PIPE).communicate()[0]).strip('\\nb\'')

        # Get current codename of Ubuntu MATE in use.
        # Uses first word in lowercase, such as : trusty, wily, xenial
        self.codename = platform.dist()[2]

        # Determine which type of session we are in.
        if os.path.exists('/usr/share/glib-2.0/schemas/zubuntu-mate-live.gschema.override'):
            self.session_type = 'live'
        elif self.user_name[:6] == 'guest-':
            self.session_type = 'guest'
        elif os.path.isfile(os.path.join('/','boot/','kernel7.img')):
            self.session_type = 'pi'
        else:
            self.session_type = 'normal'

        # To inform the user if they are running in BIOS or UEFI mode.
        if os.path.exists("/sys/firmware/efi"):
            self.boot_mode = 'UEFI'
        elif self.session_type == 'pi':
            self.boot_mode = 'Raspberry Pi'
        elif self.arch == 'powerpc':
            self.boot_mode = 'Yaboot'
        else:
            self.boot_mode = 'BIOS'

        # Create, then spawn threads
        thread1 = Thread(target=self.check_internet_connection)
        thread2 = Thread(target=self.detect_graphics)
        thread1.start()
        thread2.start()

        # Check whether Welcome is subscribed for updates.
        self.welcome_ppa_file = '/etc/apt/sources.list.d/ubuntu-mate-dev-ubuntu-welcome-' + self.codename + '.list'
        if os.path.exists(self.welcome_ppa_file):
            if os.path.getsize(self.welcome_ppa_file) > 0:
                self.updates_subscribed = True

        # Accessibility - Enlarge/shrink text based on Font DPI set by the user.
        if arg.font_dpi_override:
            font_dpi = arg.font_dpi_override
        else:
            try:
                font_value = font_gsettings.get_value('dpi')
                font_dpi = int(float(str(font_value)))
                arg.print_verbose('Welcome', 'Font DPI is: ' + str(font_dpi))
            except:
                font_dpi = 96
                print('[Welcome] Couldn\'t retrieve font DPI. Using default value of ' + str(font_dpi))

        if font_dpi < 50:
            print('[Welcome] DPI below 50. Out of range..')
            font_dpi = 96
        elif font_dpi > 500:
            print('[Welcome] DPI over 500. Out of range.')
            font_dpi = 96

        zoom_level = 1.0
        if font_dpi <= 80:
            zoom_level = 0.75
        elif font_dpi <= 87:
            zoom_level = 0.85
        elif font_dpi <= 94:
            zoom_level = 0.9
        elif font_dpi <= 101:
            zoom_level = 1.0    # Default DPI is usually 96.
        elif font_dpi <= 108:
            zoom_level = 1.1
        elif font_dpi <= 115:
            zoom_level = 1.2
        elif font_dpi <= 122:
            zoom_level = 1.3
        elif font_dpi <= 129:
            zoom_level = 1.4
        elif font_dpi >= 130:
            zoom_level = 1.5

        self.dpi = font_dpi
        self.zoom_level = zoom_level

    def check_internet_connection(self):
        print('[Network Test] Checking for internet connectivity... ')
        url = "http://pardusarm.com/"

        if arg.simulate_no_connection:
            print('[Network Test] Simulation argument override. Retrying will reset this.')
            arg.simulate_no_connection = False
            self.is_online = False
            return

        if arg.simulate_force_connection:
            print('[Network Test] Simulation argument override. Forcing connection presence.')
            print('[Network Test] WARNING: Do not attempt to install/remove software offline as this may lead to errors later!')
            arg.simulate_connection = False
            self.is_online = True
            return

        try:
            response = urllib.request.urlopen(url, timeout=2).read().decode('utf-8')
        except socket.timeout:
            print("[Network Test] -- Socket timed out to URL {0}".format(url))
            self.is_online = False
        except:
            print("[Network Test] -- Could not establish a connection to '{0}'. ".format(url))
            self.is_online = False
        else:
            print("[Network Test] Successfully pinged '{0}' ".format(url))
            self.is_online = True

    def detect_graphics(self):
        # If we're the Raspberry Pi, there is nothing to output.
        if self.session_type == 'pi':
            self.graphics_grep = 'Raspberry Pi'
            self.graphics_vendor = 'Raspberry Pi'
            return

        # TODO: Support dual graphic cards.
        arg.print_verbose('Graphics','Detecting graphics vendor... ')
        try:
            output = subprocess.Popen('lspci | grep VGA', stdout=subprocess.PIPE, shell='True').communicate()[0]
            output = output.decode(encoding='UTF-8')
        except:
            # When 'lspci' does not find a VGA controller (this is the case for the RPi 2)
            arg.print_verbose("Graphics","Couldn't detect a VGA Controller on this system.")
            output = 'Unknown'

        # Scan for and set known brand name.
        if output.find('NVIDIA') != -1:
            self.graphics_vendor = 'NVIDIA'
        elif output.find('AMD') != -1:
            self.graphics_vendor = 'AMD'
        elif output.find('Intel') != -1:
            self.graphics_vendor = 'Intel'
        elif output.find('VirtualBox') != -1:
            self.graphics_vendor = 'VirtualBox'
        else:
            self.graphics_vendor = 'Unknown'

        self.graphics_grep = repr(output)
        self.graphics_grep = self.graphics_grep.split("controller: ",1)[1]
        self.graphics_grep = self.graphics_grep.split("\\n",1)[0]
        arg.print_verbose("Graphics","Detected: {0}".format(self.graphics_grep))

    def get_system_info(self, webkit):
        print('[System Specs] Gathering system specifications...')

        # Prefixes for translation
        mb_prefix = _("MB")
        mib_prefix = _("MiB")
        gb_prefix = _("GB")
        gib_prefix = _("GiB")

        # Start collecting advanced system information in the background.
        # (Python can do other things while this command completes)
        arg.print_verbose('System Specs', 'Running "inxi" for advanced system information...')
        try:
            inxi_raw = subprocess.Popen(['inxi','-c','0','-v','5','-p','-d','-xx'], stdout=subprocess.PIPE)
        except:
            print('[System Specs] Failed to execute collect advanced information. Is "inxi" no longer installed?')

        # Append a failure symbol beforehand in event something goes horribly wrong.
        stat_error_msg = _("Could not gather data.")
        html_tag = '<a data-toggle=\'tooltip\' data-placement=\'top\' title=\'' + stat_error_msg + '\'><span class=\'fa fa-warning specs-error\'></span></a>'
        for element in ['distro', 'kernel', 'motherboard', 'boot-mode', 'cpu-model', 'cpu-speed', 'arch-use',
                        'arch-supported', 'memory', 'graphics', 'filesystem', 'capacity', 'allocated-space', 'free-space']:
            webkit.execute_script('$("#spec-' + element + '").html("' + html_tag + '")')

        # Collect basic system information
        def run_external_command(command, with_shell=False):
            if with_shell:
                raw = str(subprocess.Popen(command, stdout=subprocess.PIPE, shell=True).communicate()[0])
            else:
                raw = str(subprocess.Popen(command, stdout=subprocess.PIPE).communicate()[0])
            output = raw.replace("b'","").replace('b"',"").replace("\\n'","").replace("\\n","")
            return output

        ## Distro
        try:
            arg.print_verbose('System Specs', 'Gathering data: Distribution')
            distro_description = run_external_command(['lsb_release','-d','-s'])
            distro_codename = run_external_command(['lsb_release','-c','-s'])
            webkit.execute_script('$("#spec-distro").html("' + distro_description + '")')
        except:
            print('[System Specs] Failed to retrieve data: Distribution')

        ## Kernel
        try:
            arg.print_verbose('System Specs', 'Gathering data: Kernel')
            kernel = run_external_command(['uname','-r'])
            webkit.execute_script('$("#spec-kernel").html("' + kernel + '")')
        except:
            print('[System Specs] Failed to retrieve data: Kernel')

        ## Motherboard
        try:
            arg.print_verbose('System Specs', 'Gathering data: Motherboard')
            motherboard_name = run_external_command(['cat','/sys/devices/virtual/dmi/id/board_name'])
            webkit.execute_script('$("#spec-motherboard").html("' + motherboard_name + '")')
        except:
            print('[System Specs] Failed to retrieve data: Motherboard')

        ## CPU Details
        arg.print_verbose('System Specs', 'Gathering data: CPU')
        try:
            cpu_model = run_external_command(['lscpu | grep "name"'], True).split(': ')[1]
            webkit.execute_script('$("#spec-cpu-model").html("' + cpu_model + '")')
        except:
            print('[System Specs] Failed to retrieve data: CPU Model')

        try:
            try:
                # Try obtaining the maximum speed first.
                cpu_speed = int(run_external_command(['lscpu | grep "max"'], True).split(': ')[1].strip(' ').split('.')[0])
            except:
                # Otherwise, fetch the CPU's MHz.
                cpu_speed = int(run_external_command(['lscpu | grep "CPU MHz"'], True).split(': ')[1].strip(' ').split('.')[0])

            webkit.execute_script('$("#spec-cpu-speed").html("' + str(cpu_speed) + ' MHz")')
        except:
            print('[System Specs] Failed to retrieve data: CPU Speed')

        try:
            if self.arch == 'i386':
                cpu_arch_used = '32-bit'
            elif self.arch == 'amd64':
                cpu_arch_used = '64-bit'
            else:
                cpu_arch_used = self.arch
            webkit.execute_script('$("#spec-arch-use").html("' + cpu_arch_used + '")')
        except:
            print('[System Specs] Failed to retrieve data: CPU Arch in Use')

        try:
            cpu_arch_supported = run_external_command(['lscpu | grep "mode"'], True).split(': ')[1]
            webkit.execute_script('$("#spec-arch-supported").html("' + cpu_arch_supported + '")')
        except:
            print('[System Specs] Failed to retrieve data: CPU Supported Arch')

        ## Root partition (where Ubuntu MATE is installed) and the rest of that disk.
        try:
            if self.session_type == 'live':
                webkit.execute_script('$(".specs-hide-live-session").hide()')
            else:
                arg.print_verbose('System Specs', 'Gathering data: Storage')
                ## Gather entire disk data
                root_partition = run_external_command(['mount | grep "on / "'], True).split(' ')[0]
                if root_partition[:-2] == "/dev/sd":            # /dev/sdXY
                    root_dev = root_partition[:-1]
                if root_partition[:-2] == "/dev/hd":            # /dev/hdXY
                    root_dev = root_partition[:-1]
                if root_partition[:-3] == "/dev/mmcblk":        # /dev/mmcblkXpY
                    root_dev = root_partition[:-2]
                else:
                    root_dev = root_partition[:-1]              # Generic
                disk_dev_name = root_dev.split('/')[2]
                arg.print_verbose('System Specs', 'Ubuntu MATE is installed on disk: ' + root_dev)
                rootfs = os.statvfs('/')
                root_size = rootfs.f_blocks * rootfs.f_frsize
                root_free = rootfs.f_bavail * rootfs.f_frsize
                root_used = root_size - root_free
                entire_disk = run_external_command(['lsblk -b | grep "' + disk_dev_name + '" | grep "disk"'], True)
                entire_disk = int(entire_disk.split()[3])

                ## Perform calculations across units
                capacity_GB =   round(entire_disk/1000/1000/1000,1)
                capacity_GiB =  round(entire_disk/1024/1024/1024,1)
                allocated_GB =  round(root_size/1000/1000/1000,1)
                allocated_GiB = round(root_size/1024/1024/1024,1)
                used_GB =       round(root_used/1000/1000/1000,1)
                used_GiB =      round(root_used/1024/1024/1024,1)
                free_GB =       round(root_free/1000/1000/1000,1)
                free_GiB =      round(root_free/1024/1024/1024,1)
                other_GB =      round((entire_disk-root_size)/1000/1000/1000,1)
                other_GiB =     round((entire_disk-root_size)/1024/1024/1024,1)

                # Show megabytes/mebibytes (in red) if gigabytes are too small.
                if capacity_GB <= 1:
                    capacity_GB = str(round(entire_disk/1000/1000,1)) + ' ' + mb_prefix
                    capacity_GiB = str(round(entire_disk/1024/1024,1)) + ' ' + mib_prefix
                else:
                    capacity_GB = str(capacity_GB) + ' ' + gb_prefix
                    capacity_GiB = str(capacity_GiB) + ' ' + gib_prefix

                if allocated_GB <= 1:
                    allocated_GB =  str(round(root_size/1000/1000,1)) + ' ' + mb_prefix
                    allocated_GiB = str(round(root_size/1024/1024,1)) + ' ' + mib_prefix
                else:
                    allocated_GB = str(allocated_GB) + ' ' + gb_prefix
                    allocated_GiB = str(allocated_GiB) + ' ' + gib_prefix

                if used_GB <= 1:
                    used_GB =  str(round(root_used/1000/1000,1)) + ' ' + mb_prefix
                    used_GiB = str(round(root_used/1024/1024,1)) + ' ' + mib_prefix
                else:
                    used_GB = str(used_GB) + ' ' + gb_prefix
                    used_GiB = str(used_GiB) + ' ' + gib_prefix

                if free_GB <= 1:
                    free_GB =  str(round(root_free/1000/1000,1)) + ' ' + mb_prefix
                    free_GiB = str(round(root_free/1024/1024,1)) + ' ' + mib_prefix
                    webkit.execute_script('$("#spec-free-space").addClass("specs-error")')
                else:
                    free_GB = str(free_GB) + ' ' + gb_prefix
                    free_GiB = str(free_GiB) + ' ' + gib_prefix

                if other_GB <= 1:
                    other_GB =  str(round((entire_disk-root_size)/1000/1000,1)) + ' ' + mb_prefix
                    other_GiB = str(round((entire_disk-root_size)/1024/1024,1)) + ' ' + mib_prefix
                else:
                    other_GB = str(other_GB) + ' ' + gb_prefix
                    other_GiB = str(other_GiB) + ' ' + gib_prefix

                ## Append data to HTML.
                webkit.execute_script('$("#spec-filesystem").html("' + root_partition + '")')
                webkit.execute_script('$("#spec-capacity").html("' + capacity_GB + ' <span class=\'secondary-value\'>(' + capacity_GiB + ')</span>")')
                webkit.execute_script('$("#spec-allocated-space").html("' + allocated_GB + ' <span class=\'secondary-value\'>(' + allocated_GiB + ')</span>")')
                webkit.execute_script('$("#spec-used-space").html("' + used_GB + ' <span class=\'secondary-value\'>(' + used_GiB + ')</span>")')
                webkit.execute_script('$("#spec-free-space").html("' + free_GB + ' <span class=\'secondary-value\'>(' + free_GiB + ')</span>")')
                webkit.execute_script('$("#spec-other-space").html("' + other_GB + ' <span class=\'secondary-value\'>(' + other_GiB + ')</span>")')

                ## Calculate representation across physical disk
                disk_percent_UM_used = int(round(root_used / entire_disk * 100)) * 2
                disk_percent_UM_free = int(round(root_free / entire_disk * 100)) * 2
                disk_percent_other   = (200 - disk_percent_UM_used - disk_percent_UM_free)
                arg.print_verbose('System Specs', ' --- Disk: ' + root_dev)
                arg.print_verbose('System Specs', ' --- * OS Used: ' + str(root_used) + ' bytes (' + str(disk_percent_UM_used/2) + '%)')
                arg.print_verbose('System Specs', ' --- * OS Free: ' + str(root_free) + ' bytes (' + str(disk_percent_UM_free/2) + '%)')
                arg.print_verbose('System Specs', ' --- = Other Partitions: ' + str(entire_disk - root_size) + ' bytes (' + str(disk_percent_other/2) + '%)')
                webkit.execute_script("$('#disk-used').width('" + str(disk_percent_UM_used) + "px');")
                webkit.execute_script("$('#disk-free').width('" + str(disk_percent_UM_free) + "px');")
                webkit.execute_script("$('#disk-other').width('" + str(disk_percent_other) + "px');")
        except:
            print('[System Specs] Failed to retrieve data: Storage')

        ## RAM
        try:
            arg.print_verbose('System Specs', 'Gathering Data: RAM')
            ram_bytes = run_external_command(['free -b | grep "Mem:" '], True)
            ram_bytes = float(ram_bytes.split()[1])
            if round(ram_bytes / 1024 / 1024) < 1024:
                ram_xb = str(round(ram_bytes / 1000 / 1000, 1)) + ' ' + mb_prefix
                ram_xib = str(round(ram_bytes / 1024 / 1024, 1)) + ' ' + mib_prefix
            else:
                ram_xb =  str(round(ram_bytes / 1000 / 1000 / 1000, 1)) + ' ' + gb_prefix
                ram_xib = str(round(ram_bytes / 1024 / 1024 / 1024, 1)) + ' ' + gib_prefix
            ram_string = ram_xb + ' <span class=\'secondary-value\'>(' + ram_xib + ')</span>'
            webkit.execute_script('$("#spec-memory").html("' + ram_string + '")')
        except:
            print('[System Specs] Failed to retrieve data: RAM (Memory)')

        ## Graphics
        webkit.execute_script('$("#spec-graphics").html("' + self.graphics_grep + '")')

        ## Collect missing data differently for some architectures.
        if systemstate.arch == 'powerpc':
            ## Motherboard & Revision
            try:
                arg.print_verbose('System Specs', 'Gathering alternate data: PowerPC Motherboard')
                mb_model = run_external_command(['grep','motherboard','/proc/cpuinfo']).split(': ')[1]
                mb_rev = run_external_command(['grep','revision','/proc/cpuinfo']).split(': ')[1]
                webkit.execute_script('$("#spec-motherboard").html("' + mb_model + ' ' + mb_rev + '")')
            except:
                arg.print_verbose('System Specs', 'Failed to gather data: PowerPC Motherboard')

            ## CPU and Clock Speed
            try:
                arg.print_verbose('System Specs', 'Gathering alternate data: PowerPC CPU')
                cpu_model = run_external_command(['grep','cpu','/proc/cpuinfo']).split(': ')[1]
                cpu_speed = run_external_command(['grep','clock','/proc/cpuinfo']).split(': ')[1]
                webkit.execute_script('$("#spec-cpu-model").html("' + cpu_model + '")')
                webkit.execute_script('$("#spec-cpu-speed").html("' + str(cpu_speed) + '")')
            except:
                arg.print_verbose('System Specs', 'Failed to gather data: PowerPC CPU')

            ## Device Name
            try:
                arg.print_verbose('System Specs', 'Gathering alternate data: PowerPC Model Name')
                mb_name = run_external_command(['grep','detected','/proc/cpuinfo']).split(': ')[1]
                webkit.execute_script('$("#spec-motherboard").append(" / ' + mb_name + '")')
            except:
                arg.print_verbose('System Specs', 'Failed to gather data: PowerPC Model Name')

            ## Boot Mode / PowerMac Generation
            try:
                arg.print_verbose('System Specs', 'Gathering alternate data: PowerMac Generation')
                mac_generation = run_external_command(['grep','pmac-generation','/proc/cpuinfo']).split(': ')[1]
                webkit.execute_script('$("#spec-boot-mode").html("Yaboot (' + mac_generation + ')")')
            except:
                arg.print_verbose('System Specs', 'Failed to gather data: PowerMac Generation')

        # Append advanced system information
        try:
            arg.print_verbose('System Specs', 'Waiting for inxi process to finish...')
            inxi_output = str(inxi_raw.communicate()[0])
            inxi_output = inxi_output.replace("b'","").replace("\\n","\n")
            webkit.execute_script("$('#specs-inxi').html('')")
            for line in inxi_output.split('\n'):
                webkit.execute_script("$('#specs-inxi').append('" + line.strip('"').strip("'") + "<br>')")
            print('[System Specs] Successfully appended advanced system information.')
        except:
            print('[System Specs] Failed to append advanced system information or communicate with "inxi" process.')

        # Check internet connectivity status.
        if self.is_online:
            webkit.execute_script('$("#specs-has-net").show()')
            webkit.execute_script('$("#specs-has-no-net").hide()')
        else:
            webkit.execute_script('$("#specs-has-net").hide()')
            webkit.execute_script('$("#specs-has-no-net").show()')

        # Change icon depending on what type of device we are using.
        if self.session_type == 'pi':
            webkit.execute_script('$("#specs-device-rpi").show()')
            webkit.execute_script('$(".specs-hide-pi").hide()')
        elif self.arch == 'powerpc':
            webkit.execute_script('$("#specs-device-powerpc").show()')
            webkit.execute_script('$(".specs-hide-ppc").hide()')
        elif self.graphics_vendor == 'VirtualBox':
            webkit.execute_script('$("#specs-device-vbox").show()')
            webkit.execute_script('$(".specs-hide-vbox").hide()')
        elif self.session_type == 'live':
            webkit.execute_script('$("#specs-live-session").show()')
            webkit.execute_script('$(".specs-hide-live").hide()')
        else:
            webkit.execute_script('$("#specs-device-normal").show()')

        # Display UEFI/BIOS boot mode.
        if systemstate.arch == 'i386' or systemstate.arch == 'amd64':
            webkit.execute_script('$("#spec-boot-mode").html("' + self.boot_mode + '")')

        # Hide root storage info if in a live session.
        if self.session_type == 'live':
            webkit.execute_script('$(".spec-3").hide()')

        # Data cached, ready to display.
        webkit.execute_script('$("#specs-loading").fadeOut("fast")')
        webkit.execute_script('$("#specs-tabs").fadeIn("fast")')
        webkit.execute_script('$("#specs-basic").fadeIn("medium")')
        webkit.execute_script('setCursorNormal()')

    def rpi_resize(self, action, webkit=None):
        if action == 'do-resize':
            subprocess.call(['pkexec', '/usr/lib/ubuntu-mate/ubuntu-mate-welcome-rpi2-partition-resize'])

            def notify(subject, body, icon):
                Notify.init(_('Raspberry Pi Partition Resize'))
                resize_notify=Notify.Notification.new(subject, body, icon)
                resize_notify.show()

            try:
                with open('/tmp/notify_rpi_status') as status_file:
                    status_code = int(status_file.read())
            except:
                status_code = 0

            try:
                with open('/tmp/notify_rpi_text') as misc_file:
                    misc_text = misc_file.read()
            except:
                misc_text = ""

            if status_code == 1:
                notify( _("Root partition has been resized."), _("The filesystem will be enlarged upon the next reboot."), 'dialog-information' )
                self.rpi_resize_pending = True
                webkit.execute_script('$("#rpi-resized").hide()')
                webkit.execute_script('$("#rpi-not-resized").hide()')
                webkit.execute_script('$("#rpi-restart-now").show()')
            elif status_code == 2:
                notify( _("Don't know how to expand."), misc_text + ' ' + _("does not exist or is not a symlink."), 'dialog-error' )
            elif status_code == 3:
                notify( _("Don't know how to expand."), misc_text + ' ' + _("is not an SD card."), 'dialog-error' )
            elif status_code == 4:
                notify( _("Don't know how to expand."), _("Your partition layout is not currently supported by this tool."), 'dialog-error' )
            elif status_code == 5:
                notify( _("Don't know how to expand."), misc_text + ' ' + _("is not the last partition."), 'dialog-error' )
            else:
                notify( _("Failed to run resize script."), _("The returned error code is:") + str(status_code), 'dialog-error' )
                print('[Welcome] Unrecognised return code for Raspberry Pi resize: ' + str(status_code))

            app._appView._push_config()

        elif action == 'check':
            if os.path.exists('/.resized'):
                resized = True
            else:
                resized = False

            if resized:
                webkit.execute_script('$("#rpi-resized").show()')
                webkit.execute_script('$("#rpi-not-resized").hide()')
            else:
                webkit.execute_script('$("#rpi-resized").hide()')
                webkit.execute_script('$("#rpi-not-resized").show()')

            if self.rpi_resize_pending:
                webkit.execute_script('$("#rpi-resized").hide()')
                webkit.execute_script('$("#rpi-not-resized").hide()')
                webkit.execute_script('$("#rpi-restart-now").show()')

        elif action == 'reboot':
            subprocess.call(['mate-session-save','--shutdown-dialog'])


class DynamicApps(object):
    def __init__(self):
        # Load JSON Index into Memory
        self.reload_index()

        # Variables to remember common details.
        self.all_categories = ['Accessories', 'Education', 'Games', 'Graphics', 'Internet', 'Office', 'Programming', 'Media', 'SysTools', 'UnivAccess', 'Servers', 'MoreApps']
        self.hide_non_free = False

        # Reading the apt cache later.
        self._apt_cache = apt.Cache()

        # Indicate that operations are in progress.
        self.operations_busy = False

        # Get the version of Welcome in use.
        for pkgname in self._apt_cache.keys():
            if 'ubuntu-mate-welcome' in pkgname:
                systemstate.welcome_version = "v3.17"
                break
        print('[Welcome] Version: ' + systemstate.welcome_version)


    ###### JSON Index Structure
    #
    #   ===== Structure Overview =====
    #   {
    #     "Category" {                                  - Application category.
    #       "application-id" {                          - Unique string identifier for application, apps sorted A-Z. Hyphens preferred.
    #         "variable": "data",                       - Variable containing single data.
    #         "list": ["This is a line.",
    #                   "The same line."]               - Variable containing a 'list' of data.
    #         "group": { "variable": "data" }           - Group containing data.
    #        }
    #      }
    #   }
    #
    #   ** Standard JSON rules apply. Watch out for the commas.
    #   ** Important!! Use &#8217; instead of ' for an apostrophe character.

    #   ===== Variable Index =====
    #   Variable                Type        Required?   Description
    #   ----------------------- ----------  ----------  ---------------------------------------------
    #   name                    string      Yes         Name of the application as displayed to the user.
    #   img                     string      Yes         Name of image. Excluding ".png" extension.
    #   main-package            string      Yes         Package used to detect if it's installed.
    #   launch-command          string      No          Command to launch the installed program. Can be ignored for no launch option.
    #   install-packages        string      *           Packages to install/reinstall. Comma separated.
    #   remove-packages         string      *           Packages to remove. Comma separated.
    #   upgradable              boolean     *           This package is only for upgrading.
    #   upgrade-packages        string      *           Packages to upgrade. Comma separated.
    #   description             list        Yes         Description of the application. Use usual HTML tags for formatting. Can be left blank if unlisted.
    #   alternate-to            string      No          If the app is similar or has an alternate. Can be ignored.
    #   subcategory             string      Yes         Used for filtering applications within the category. Eg. "Partitioning", "Audio Production".
    #   open-source             boolean     Yes         Proprietary or Open Source?
    #   url-info                string      Yes         URL to the web page for more information.
    #   url-android             string      No          URL if there is an associated Android app. Can be ignored.
    #   url-ios                 string      No          URL if there is an associated Android app. Can be ignored.
    #   arch                    string      Yes         Supported architectures for this app. Comma seperated.
    #   releases                string      Yes         Supported versions of Ubuntu MATE to show this application. Comma seperated.
    #   working                 boolean     Yes         Show/hide visibility of this application.
    #   notes                   string      No          Optional developer notes for the application.
    #
    #### * Only if applicable to application.

    #   ===== Pre-installation Index =====
    #
    #   "pre-install": {                                - Required group of data containing pre-installation procedures.
    #       "trusty": {                                 - Different releases may have different operations.
    #           "variable":  "data"                     - See table below for possible operations.
    #       },
    #       "all": {                                    - Use "all" to specify all other releases. This should be last.
    #           "variable":  "data"                         If there is only one instruction,
    #       }
    #   }
    #
    #   method                  string      Yes         Pre-configuration methods. Multiple can be specified with a plus '+'.
    #                                                       "skip"          =   Package is already in archive.
    #                                                       "ppa"           =   Add a PPA. Specify (2), optionally (1).
    #                                                       "partner-repo"  =   Add the Ubuntu Partners Repo.
    #                                                       "manual"        =   Get keys and write a sources.list file. (3)
    #   source-file        (1)  string      No          Source file to update, excluding the ".list" extension.
    #   enable-ppa         (2)  string      *           Name of PPA to add, eg. "ppa:somebody/someapp".
    #   apt-key-url        (3)  string      *           Retrieve the key from URL.
    #   apt-key-server     (3)  list        *           Retrieve the key from a server.
    #                                                       "server-address"    = Eg. "keyserver.ubuntu.com"
    #                                                       "key"               = Eg. "D2C19886"
    #   apt-sources        (3)  list        *           Contents for the sources file. Each variable is a new line.
    #
    #### These keys words can be given as placeholders:
    #
    #   CODENAME        =   Current Ubuntu release, eg. "xenial".
    #   OSVERSION       =   Current Ubuntu version, eg "v3.17".
    #

    def reload_index(self):
        try:
            print('[Apps] Reading index...')
            json_path = os.path.abspath(os.path.join(app._data_path, 'js/applications.json'))
            with open(json_path) as data_file:
                self.index = json.load(data_file)
                print('[Apps] Successfully loaded index.')
        except Exception as e:
            self.index = None
            print("[Apps] ERROR: Software Index JSON is invalid or missing!")
            print("------------------------------------------------------------")
            print("Exception:")
            print(str(e))
            print("------------------------------------------------------------")

    def set_app_info(self, category, program_id):
        self.app_name = self.index[category][program_id]['name']
        self.app_img = self.index[category][program_id]['img']
        self.app_main_package = self.index[category][program_id]['main-package']
        self.app_launch_command = self.index[category][program_id]['launch-command']
        self.app_upgrade_only = False
        try:
            if self.index[category][program_id]['upgradable']:
                self.app_upgrade_only = True
                self.app_upgrade_packages = self.index[category][program_id]['upgrade-packages']
        except:
            self.app_upgrade_only = False

        if not self.app_upgrade_only:
            self.app_install_packages = self.index[category][program_id]['install-packages']
            self.app_remove_packages = self.index[category][program_id]['remove-packages']
        self.app_description = ''
        for line in self.index[category][program_id]['description']:
            self.app_description = self.app_description + ' ' + line
        self.app_alternate_to = self.index[category][program_id]['alternate-to']
        self.app_subcategory = self.index[category][program_id]['subcategory']
        self.app_open_source = self.index[category][program_id]['open-source']
        self.app_url_info = self.index[category][program_id]['url-info']
        self.app_url_android = self.index[category][program_id]['url-android']
        self.app_url_ios = self.index[category][program_id]['url-ios']
        self.app_arch = self.index[category][program_id]['arch']
        self.app_releases = self.index[category][program_id]['releases']
        self.app_working = self.index[category][program_id]['working']

    def populate_categories(self, webkit):
        ''' List all of the applications supported on the current architecture. '''
        total_added = 0
        total_skipped = 0
        total_unsupported = 0

        # Don't attempt to continue if the index is missing/incorrectly parsed.
        if not self.index:
            print('[Apps] ERROR: Application index not loaded. Cannot populate categories.')
            return

        # Strings
        str_nothing_here = _("Sorry, Welcome could not feature any software for this category that is compatible on this system.")
        str_upgraded = _("This application is set to receive the latest updates.")
        str_alternate_to = _('Alternative to:')
        str_hide = _("Hide")
        str_show = _("Show")
        str_install = _("Install")
        str_reinstall = _("Reinstall")
        str_remove = _("Remove")
        str_upgrade = _("Upgrade")
        str_launch = _("Launch")
        str_license = _("License")
        str_platform = _("Platform")
        str_category = _("Category")
        str_website = _("Website")
        str_screenshot = _("Screenshot")
        str_source = _("Source")
        str_source_ppa = '<span class="fa fa-cube"></span>&nbsp;'
        str_source_manual = '<span class="fa fa-globe"></span></a>&nbsp;'
        str_source_partner = '<img src="img/logos/ubuntu-mono.png" width="16px" height="16px"/>&nbsp;' + _('Canonical Partner Repository')
        str_source_skip = '<img src="img/logos/ubuntu-mono.png" width="16px" height="16px"/>&nbsp;' + _('Ubuntu Repository')
        str_unknown = _('Unknown')

        # Get the app data from each category and list them.
        for category in self.all_categories:
            arg.print_verbose('Apps', ' ------ Processing: ' + category + ' ------')

            # Convert to a list to work with. Sort alphabetically.
            category_items = list(self.index[category].keys())
            category_items.sort()

            # Keep a count of apps in case there are none to list.
            apps_here = 0

            # Keep track of the subcategories of the apps in this category so we can filter them.
            subcategories = []

            # Enumerate each program in this category.
            for program_id in category_items:
                self.set_app_info(category, program_id)

                # Only list the program if it's working.
                if not self.app_working:
                    arg.print_verbose('Apps', ' Unlisted: ' + self.app_name)
                    total_skipped = total_skipped + 1
                    continue

                # Only list the program if it supports the current architecture in use.
                supported = False
                supported_arch = False
                supported_release = False

                for architecture in self.app_arch.split(','):
                    if architecture == systemstate.arch:
                        supported_arch = True

                # Only list the program if it's available for the current release.
                for release in self.app_releases.split(','):
                    if release == systemstate.codename:
                        supported_release = True

                if supported_arch and supported_release:
                    supported = True

                if not supported:
                    arg.print_verbose('Apps', ' Unsupported: ' + self.app_name + ' (Only for architectures: ' + self.app_arch + ' and releases: ' + self.app_releases + ')' )
                    total_unsupported = total_unsupported + 1
                    continue

                # If the app has made it this far, it can be added to the grid.
                # CSS breaks with dots (.), so any must become hyphens (-).
                arg.print_verbose('Apps', ' Added: ' + self.app_name)
                subcategories.append(self.app_subcategory)
                html_buffer = ''
                css_class = program_id.replace('.','-')
                css_subcategory = self.app_subcategory.replace(' ','-')

                # "Normal" packages that can be installed/removed by the user.
                if self.app_open_source:
                    html_buffer = html_buffer + '<div id="' + css_class + '" class="app-entry filter-' + css_subcategory + '">'
                else:
                    html_buffer = html_buffer + '<div id="' + css_class + '" class="app-entry filter-' + css_subcategory + ' proprietary">'
                html_buffer = html_buffer + '<div class="row-fluid">'
                html_buffer = html_buffer + '<div class="span2 center-inside">'
                html_buffer = html_buffer + '<img src="img/applications/' + self.app_img + '.png">'
                html_buffer = html_buffer + '<span class="fa fa-check-circle fa-2x installed-check ' + css_class + '-remove"></span>'
                html_buffer = html_buffer + '</div><div class="span10">'
                html_buffer = html_buffer + '<p><b class="' + css_class + '-text">' + self.app_name + '</b></p>'
                html_buffer = html_buffer + '<p class="' + css_class + '-text">' + self.app_description + '</p>'

                # Check any "Upgrade" packages if the PPA has already been added.
                upgraded = False
                if self.app_upgrade_only:
                    try:
                        listname = dynamicapps.index[category][program_id]['pre-install']['all']['source-file']
                        listname = listname.replace('OSVERSION',preinstallation.os_version).replace('CODENAME',preinstallation.codename)
                        if os.path.exists(os.path.join('/', 'etc', 'apt', 'sources.list.d', listname+'.list')):
                            upgraded = True
                            html_buffer = html_buffer + '<h5 class="' + css_class + '-text"><span class="fa fa-check-circle"></span> ' + str_upgraded + '</h5>'
                    except:
                        pass

                if not self.app_alternate_to == None:
                    html_buffer = html_buffer + '<ul><li class="' + css_class + '-text"><b>' + str_alternate_to + ' </b><i>' + self.app_alternate_to + '</i></li></ul>'
                html_buffer = html_buffer + '<p class="text-right">'
                html_buffer = html_buffer + '<a id="info-show-' + css_class + '" class="btn" href="cmd://app-info-show?' + css_class + '"><span class="fa fa-chevron-down"></span> ' + str_show + '</a>&nbsp;'
                html_buffer = html_buffer + '<a hidden id="info-hide-' + css_class + '" class="btn" href="cmd://app-info-hide?' + css_class + '"><span class="fa fa-chevron-up"></span> ' + str_hide + '</a>&nbsp;'

                # "Regular" packages - can be installed or removed with one-click by the user.
                if not self.app_upgrade_only:
                    html_buffer = html_buffer + '<span class="' + css_class + '-applying"> <span class="' + css_class + '-applying-status"></span> &nbsp;<img src="img/welcome/processing.gif" width="24px" height="24px"/></span>'
                    html_buffer = html_buffer + '<a class="' + css_class + '-install btn btn-success" href="cmd://install-appid?' + program_id + '"><span class="fa fa-download"></span>&nbsp; ' + str_install + '</a>&nbsp;'
                    html_buffer = html_buffer + '<a class="' + css_class + '-reinstall btn btn-warning" href="cmd://install-appid?' + program_id + '" data-toggle="tooltip" data-placement="top" title="' + str_reinstall + '"><span class="fa fa-refresh"></span></a>&nbsp;'
                    html_buffer = html_buffer + '<a class="' + css_class + '-remove btn btn-danger" href="cmd://remove-appid?' + program_id + '" data-toggle="tooltip" data-placement="top" title="' + str_remove + '"><span class="fa fa-trash"></span></a>&nbsp;'

                # "Upgradable" packages - usually pre-installed but have a more up-to-date repository.
                if self.app_upgrade_only:
                    arg.print_verbose('Apps', 'Upgrade: ' + self.app_name)
                    if not upgraded:
                        html_buffer = html_buffer + '<a class="' + css_class + '-upgrade btn btn-warning" href="cmd://upgrade-appid?' + program_id + '"><span class="fa fa-level-up"></span>&nbsp; ' + str_upgrade + '</a>&nbsp;'

                if not self.app_launch_command == None:
                    html_buffer = html_buffer + '<a class="' + css_class + '-launch btn btn-inverse" href="cmd://launch-appid?' + program_id + '"><img src="img/applications/' + self.app_img + '.png" width="20px" height="20px" />&nbsp; ' + str_launch + '</a>&nbsp;'

                # More details section.
                html_buffer = html_buffer + '</p><div hidden id="details-' + css_class + '">'

                ## Determine string for license
                if self.app_open_source:
                    license_string = _('Open Source')
                else:
                    license_string = _('Proprietary')

                ## Determine supported platforms
                platform_string = ''
                for arch in self.app_arch.split(','):
                    if arch == 'i386':
                        platform_string = platform_string + '<span class="i386"><span class="i386 fa fa-laptop"></span> 32-bit</span> &nbsp;&nbsp;'
                    elif arch =='amd64':
                        platform_string = platform_string + '<span class="amd64"><span class="fa fa-laptop"></span> 64-bit</span> &nbsp;&nbsp;'
                    elif arch =='armhf':
                        platform_string = platform_string + '<span class="armhf"><span class="fa fa-tv"></span> aarch32 (ARMv7)</span> &nbsp;&nbsp;'
                    elif arch =='powerpc':
                        platform_string = platform_string + '<span class="powerpc"><span class="fa fa-desktop"></span> PowerPC</span> &nbsp;&nbsp;'

                ## Add Android / iOS app links if necessary.
                if not self.app_url_android == None:
                    platform_string = platform_string + '<a href="cmd://link?' + self.app_url_android + '"><span class="fa fa-android"></span> Android</a> &nbsp;&nbsp;'

                if not self.app_url_ios == None:
                    platform_string = platform_string + '<a href="cmd://link?' + self.app_url_ios + '"><span class="fa fa-apple"></span> iOS</a> &nbsp;&nbsp;'

                ## Add details about the source of this file.
                try:
                    preinstall = dynamicapps.index[category][program_id]['pre-install']
                    codenames = list(preinstall.keys())
                    target = None
                    for name in codenames:
                        if name == systemstate.codename:
                            target = name
                            break
                    if not target:
                            target = 'all'

                    methods = preinstall[target]['method'].split('+')
                    self.source_info = []
                    if len(methods) > 1:
                        multiple_sources = True
                    else:
                        multiple_sources = False

                    for method in methods:
                        if method == 'skip':
                            self.source_info.insert(0, str_source_skip)

                        elif method == 'partner-repo':
                            self.source_info.insert(0, str_source_partner)

                        elif method == 'ppa':
                            ppa = preinstall[target]['enable-ppa']
                            ppa_author = ppa.split(':')[1].split('/')[0]
                            ppa_archive = ppa.split(':')[1].split('/')[1]
                            self.source_info.insert(0, str_source_ppa + ' <a href="cmd://link?https://launchpad.net/~' + ppa_author + '/+archive/ubuntu/' + ppa_archive + '">' + ppa + '</a>')

                        elif method == 'manual':
                            apt_source = ''.join(preinstall[target]['apt-sources'])
                            manual_text = str_source_manual + ' ' + str_unknown
                            for substring in apt_source.split(' '):
                                if substring[:4] == 'http':
                                    apt_source = substring.replace('OSVERSION',preinstallation.os_version).replace('CODENAME',preinstallation.codename)
                                    manual_text = str_source_manual + ' ' + apt_source
                                    break
                            self.source_info.insert(0, manual_text)

                except:
                    print('[Apps] WARNING: Error occurred while processing pre-configuration! Skipped Source: ' + program_id)
                    self.source_info = [str_unknown]

                ## Write contents of the table.
                html_buffer = html_buffer + '<table class="more-details table table-striped">'
                html_buffer = html_buffer + '<tr><th>' + str_license + '</th><td>' + license_string + '</td></tr>'
                html_buffer = html_buffer + '<tr><th>' + str_platform + '</th><td>' + platform_string + '</td></tr>'
                html_buffer = html_buffer + '<tr><th>' + str_category + '</th><td>' + self.app_subcategory + '</td></tr>'

                ## Add a website URL if there is one.
                if self.app_url_info:
                    html_buffer = html_buffer + '<tr><th>' + str_website + '</th><td><a href="cmd://link?' + self.app_url_info + '">' + self.app_url_info + '</a></td></tr>'

                ## Add the source for this application.
                if multiple_sources:
                    html_buffer = html_buffer + '<tr><th>' + str_source + '</th><td><ul>'
                    for item in self.source_info:
                        html_buffer = html_buffer + '<li>' + item + '</li>'
                    html_buffer = html_buffer + '</td></tr></ul>'
                else:
                    html_buffer = html_buffer + '<tr><th>' + str_source + '</th><td>' + self.source_info[0] + '</td></tr>'

                ## Add a screenshot if there is any.
                ## Images should be labelled the same as 'img' and increment starting at 1.
                screenshots = 1
                screenshots_end = False
                screenshot_buffer = ''
                while not screenshots_end:
                    screenshot_path = os.path.join(app._data_path + 'img/applications/screenshots/' + self.app_img + '-' + str(screenshots) + '.jpg')
                    if os.path.exists(screenshot_path):
                        screenshot_buffer = screenshot_buffer + '<a class="screenshot-link" href="cmd://screenshot?' + self.app_img + '-' + str(screenshots) + '"><img src="' + screenshot_path + '" class="screenshot"/></a>'
                        screenshots = screenshots + 1
                    else:
                        screenshots_end = True

                if not screenshots == 1:
                    html_buffer = html_buffer + '<tr><th>' + str_screenshot + '</th><td>' + screenshot_buffer + '</td></tr>'

                html_buffer = html_buffer + '</table>'

                # End the div's for this application.
                html_buffer = html_buffer + '</div><br><hr class="soften"></div></div></div>'

                # Append buffer to page
                webkit.execute_script('$("#' + category + '").append(\'' + html_buffer + '\')')
                webkit.execute_script('$("#info-hide-' + css_class + '").hide()')

                # Keep track of how many apps added.
                apps_here = apps_here + 1
                total_added = total_added + 1

            # Display a message if there is nothing for this category.
            if apps_here == 0:
                webkit.execute_script('$("#' + category + '").append("<p class=\'center\'><span class=\'fa fa-warning\'></span>&nbsp; ' + str_nothing_here + '</p>")')

            # Post actions to page
            ## Colour the architecture currently in use.
            webkit.execute_script('$(".' + systemstate.arch + '").addClass("arch-in-use")')

            # Process filters for this category.
            filters = list(set(subcategories))
            filters.sort()
            for string in filters:
                css_subcategory = string.replace(' ','-')
                webkit.execute_script('$("#Filter-' + category + '").append(\'<option value="' + css_subcategory + '">' + string + '</option>\')')

        # "Stats for nerds"
        total_apps = total_added + total_skipped + total_unsupported
        arg.print_verbose('Apps','------------------')
        arg.print_verbose('Apps','Applications added: ' + str(total_added))
        arg.print_verbose('Apps','Applications unsupported on this architecture: ' + str(total_unsupported))
        arg.print_verbose('Apps','Applications that are broken or not suitable for inclusion: ' + str(total_skipped))
        arg.print_verbose('Apps','Total number of applications: ' + str(total_apps))
        arg.print_verbose('Apps','------------------')

    def populate_featured_apps(self, webkit):
        arg.print_verbose('Apps', '---- Populating Featured Apps Grid ----')
        # Randomly generate a list of apps to feature if supported on this architecture.
        possible_apps = []
        for category in self.all_categories:
            category_items = list(self.index[category].keys())
            for program_id in category_items:
                if systemstate.arch in self.index[category][program_id]['arch']:
                    possible_apps.append(self.index[category][program_id]['img'])

        random.shuffle(possible_apps)
        for no in range(0,17):
            arg.print_verbose('Apps', str(no) + '. ' + possible_apps[no])
            webkit.execute_script("addToGrid('" + possible_apps[no] + "');")
        webkit.execute_script("initGrid();")
        arg.print_verbose('Apps','------------------')

    def modify_app(self, webkit, action, program_id):
        ''' Installs, removes or upgrades an application. '''
        # Indicate changes are in progress.
        css_class = program_id.replace('.','-')
        webkit.execute_script("$('." + css_class + "-applying').show();")
        webkit.execute_script("$('." + css_class + "-launch').hide();")
        webkit.execute_script("$('." + css_class + "-install').hide();")
        webkit.execute_script("$('." + css_class + "-reinstall').hide();")
        webkit.execute_script("$('." + css_class + "-remove').hide();")
        webkit.execute_script("$('." + css_class + "-upgrade').hide();")
        webkit.execute_script("$('." + css_class + "-text').css('color','#000');")

        # Text to display when applying changes.
        install_text = _("Installing...")
        remove_text = _("Removing...")
        upgrade_text = _("Upgrading...")

        # Asynchronous apt process
        if action == 'install':
            webkit.execute_script("$('." + css_class + "-applying-status').html('" + install_text + "');")
            preinstallation.process_packages(program_id, 'install')
        elif action == 'remove':
            webkit.execute_script("$('." + css_class + "-applying-status').html('" + remove_text + "');")
            preinstallation.process_packages(program_id, 'remove')
        elif action == 'upgrade':
            webkit.execute_script("$('." + css_class + "-applying-status').html('" + upgrade_text + "');")
            preinstallation.process_packages(program_id, 'upgrade')
        else:
            print('[Apps] An unknown action was requested.')

        # Refresh the page to reflect changes (if any).
        self._apt_cache.close()
        self._apt_cache = apt.Cache()
        self.update_app_status(webkit, program_id)

    def update_app_status(self, webkit, program_id):
        ''' Update the web page for an individual application. '''

        # Don't attempt to continue if the index is missing/incorrectly parsed.
        if not self.index:
            print('[Apps] ERROR: Application index not loaded. Cannot update application status.')
            return

        # Check whether the application is installed or not.
        main_package = self.get_attribute_for_app(program_id, 'main-package')
        try:
            if self._apt_cache[main_package].is_installed:
                this_installed = True
                arg.print_verbose('Apps', '  Installed: ' + main_package)
            else:
                this_installed = False
                arg.print_verbose('Apps', 'Not present: ' + main_package)
        except:
            this_installed = False
            arg.print_verbose('Apps', 'Not present: ' + main_package)

        # Replace any dots with dashes, as they are unsupported in CSS.
        css_class = program_id.replace('.','-')

        # Update appearance on this page.
        webkit.execute_script("$('." + css_class + "-applying').hide();")
        if this_installed:
            webkit.execute_script("$('." + css_class + "-launch').show();")
            webkit.execute_script("$('." + css_class + "-install').hide();")
            webkit.execute_script("$('." + css_class + "-reinstall').show();")
            webkit.execute_script("$('." + css_class + "-remove').show();")
            webkit.execute_script("$('." + css_class + "-upgrade').show();")
        else:
            webkit.execute_script("$('." + css_class + "-launch').hide();")
            webkit.execute_script("$('." + css_class + "-install').show();")
            webkit.execute_script("$('." + css_class + "-reinstall').hide();")
            webkit.execute_script("$('." + css_class + "-remove').hide();")
            webkit.execute_script("$('." + css_class + "-upgrade').hide();")

    def update_all_app_status(self, webkit):
        ''' Update the webpage whether all indexed applications are installed or not. '''

        # Don't attempt to continue if the index is missing/incorrectly parsed.
        if not self.index:
            print('[Apps] ERROR: Application index not loaded. Cannot update page.')
            return

        # Enumerate each program and check each one from the index.
        arg.print_verbose('Apps', '---- Checking cache for installed applications ----')
        for category in self.all_categories:
            category_items = list(self.index[category].keys())
            for program_id in category_items:
                main_package = self.index[category][program_id]['main-package']
                # Only check if it's supported on this architecture.
                if systemstate.arch in self.index[category][program_id]['arch']:
                    self.update_app_status(webkit, program_id)
                else:
                    continue

        arg.print_verbose('Apps', '----------------------------------------')

    def get_attribute_for_app(self, requested_id, attribute):
        ''' Retrieves a specific attribute from a listed application,
            without specifying its category. '''
        for category in list(self.index.keys()):
            category_items = list(self.index[category].keys())
            for program_id in category_items:
                if program_id == requested_id:
                    if not attribute == 'category':
                        return self.index[category][program_id][attribute]
                    else:
                        return category

    def launch_app(self, appid):
        ''' Launch an application directly from Welcome '''
        program_name = self.get_attribute_for_app(appid, 'name')
        program_command = self.get_attribute_for_app(appid, 'launch-command')
        print('[Apps] Launched "' + program_name + '" (Command: "' + program_command + '").')
        try:
            subprocess.Popen(program_command.split(' '))
        except:
            print('[Apps] Failed to launch command: ' + program_command)
            title = _("Software Boutique")
            ok_label = _("OK")
            text_error = _("An error occurred while launching PROGRAM_NAME. Please consider re-installing the application.").replace('PROGRAM_NAME', program_name) + \
                            '\n\n' + _("Command:") + ' "' + program_command + '"'
            messagebox = subprocess.Popen(['zenity',
                         '--error',
                         '--title=' + title,
                         "--text=" + text_error,
                         "--ok-label=" + ok_label,
                         '--window-icon=error',
                         '--timeout=15'])

    def apply_filter(self, webkit, filter_value, nonfree_toggle=False):
        sub_css_class = 'filter-' + filter_value

        # Toggle visibility of non-free software.
        if nonfree_toggle:
            if self.hide_non_free:
                self.hide_non_free = False
                webkit.execute_script('$("#nonFreeCheckBox").addClass("fa-square");')
                webkit.execute_script('$("#nonFreeCheckBox").removeClass("fa-check-square");')
            else:
                self.hide_non_free = True
                webkit.execute_script('$("#nonFreeCheckBox").removeClass("fa-square");')
                webkit.execute_script('$("#nonFreeCheckBox").addClass("fa-check-square");')

        if filter_value == 'none':
            arg.print_verbose('Apps','Filter reset.')
            webkit.execute_script('$(".app-entry").show();')
            if self.hide_non_free:
                arg.print_verbose('Apps','Hiding all proprietary software.')
                webkit.execute_script('$(".proprietary").hide();')
            return
        else:
            arg.print_verbose('Apps','Applying filter: ' + filter_value)
            webkit.execute_script('$(".app-entry").hide();')

            for category in self.all_categories:
                category_items = list(self.index[category].keys())
                for program_id in category_items:
                    app_subcategory = self.index[category][program_id]['subcategory'].replace(' ','-')
                    app_open_source = self.index[category][program_id]['open-source']

                    # If the application is closed source and we're told to hide it.
                    if not app_open_source and self.hide_non_free:
                        webkit.execute_script('$("#' + program_id.replace('.','-') + '").hide();')
                        continue

                    # Only show if subcategory matches.
                    if app_subcategory.replace(' ','-') == filter_value:
                        webkit.execute_script('$("#' + program_id.replace('.','-') + '").show();')

    def show_screenshot(self, filename):
        ssw = ScreenshotWindow(filename)


class ScreenshotWindow(Gtk.Window):
    ''' Displays a simple window when enlarging a screenshot. '''

    # FIXME: Destroy this window when finished as it prevents the app from closing via the "Close" button and bloats memory.

    def __init__(self, filename):
        # Strings for this child window.
        title_string = 'Preview Screenshot'
        close_string = 'Close'
        path = app._data_path + '/img/applications/screenshots/' + filename + '.jpg'

        # Build a basic pop up window containing the screenshot at its full dimensions.
        Gtk.Window.__init__(self, title=title_string)
        self.overlay = Gtk.Overlay()
        self.add(self.overlay)
        self.background = Gtk.Image.new_from_file(path)
        self.overlay.add(self.background)
        self.grid = Gtk.Grid()
        self.overlay.add_overlay(self.grid)
        self.connect('button-press-event', self.destroy_window)      # Click anywhere to close the window.
        self.connect('delete-event', Gtk.main_quit)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(False)
        # FIXME: Set the cursor to a hand, like it was a link.
        #~ self.get_root_window().set_cursor(Gdk.Cursor(Gdk.CursorType.HAND1))
        self.show_all()
        Gtk.main()

    def destroy_window(self, widget, dummy=None):
        self.destroy()


class Arguments(object):
    '''Check arguments passed the application.'''

    def __init__(self):
        self.verbose_enabled = False
        self.simulate_arch = None
        self.simulate_session = None
        self.simulate_codename = None
        self.simulate_no_connection = False
        self.simulate_force_connection = False
        self.jump_software_page = False
        self.simulate_software_changes = False
        self.locale = None
        self.jump_to = None
        self.font_dpi_override = None

        for arg in sys.argv:
          if arg == '--help':
              print('\nUbuntu MATE Welcome Parameters\n  Intended for debugging and testing purposes only!\n')
              print('\nUsage: ubuntu-mate-welcome [arguments]')
              print('  -v  --verbose               Show more details.')
              print('  --force-arch=<ARCH>         Simulate a specific architecture.')
              print('                                 "i386", "amd64" or "armhf" or "powerpc"')
              print('  --force-session=<TYPE>      Simulate a specific architecture.')
              print('                                 "guest", "live" or "pi" or "vbox"')
              print('  --force-codename=<NAME>     Simulate a specific Ubuntu MATE codename release.')
              print('                                 Examples: "trusty", "wily" or "xenial"')
              print('  --force-no-net              Simulate no internet connection.')
              print('  --force-net                 Simulate a working internet connection.')
              print('  --software-only             Open Welcome only for the software selections.')
              print('  --simulate-changes          Simulate software package changes without modifying the system.')
              print('  --locale=<LOCALE>           Locale to use e.g. fr_FR.')
              print('  --jump-to=<page>            Open a specific page, excluding html extension.')
              print('  --font-dpi=<number>         Override the font size by specifying a font DPI.')
              print('')
              exit()

          if arg == '--verbose' or arg == '-v':
              print('[Debug] Verbose mode enabled.')
              self.verbose_enabled = True

          if arg.startswith('--force-arch'):
              try:
                  self.simulate_arch = arg.split('--force-arch=')[1]
                  if not self.simulate_arch == 'i386' and not self.simulate_arch == 'amd64' and not self.simulate_arch == 'armhf' and not self.simulate_arch == 'powerpc':
                      print('[Debug] Unrecognised architecture: ' + self.simulate_arch)
                      exit()
                  else:
                      print('[Debug] Simulating architecture: ' + self.simulate_arch)
              except:
                  print('[Debug] Invalid arguments for "--force-arch"')
                  exit()

          if arg.startswith('--force-session'):
              try:
                  self.simulate_session = arg.split('--force-session=')[1]
                  if not self.simulate_session == 'guest' and not self.simulate_session == 'live' and not self.simulate_session == 'pi' and not self.simulate_session == 'vbox':
                      print('[Debug] Unrecognised session type: ' + self.simulate_session)
                      exit()
                  else:
                      print('[Debug] Simulating session: ' + self.simulate_session)
              except:
                  print('[Debug] Invalid arguments for "--force-session"')
                  exit()

          if arg.startswith('--force-codename'):
              self.simulate_codename = arg.split('--force-codename=')[1]
              print('[Debug] Simulating Ubuntu MATE release: ' + self.simulate_codename)

          if arg == '--force-no-net':
              print('[Debug] Simulating the application without an internet connection.')
              self.simulate_no_connection = True

          if arg == '--force-net':
              print('[Debug] Forcing the application to think we\'re connected with an internet connection.')
              self.simulate_force_connection = True

          if arg == '--software-only':
              print('[Welcome] Starting in software selections only mode.')
              self.jump_software_page = True

          if arg == '--simulate-changes':
              print('[Debug] Any changes to software will be simulated without modifying the actual system.')
              self.simulate_software_changes = True

          if arg.startswith('--locale='):
              self.locale = arg.split('--locale=')[1]
              print('[Debug] Setting locale to: ' + self.locale)

          if arg.startswith('--jump-to='):
              self.jump_to = arg.split('--jump-to=')[1]
              print('[Debug] Opening page: ' + self.jump_to + '.html')

          if arg.startswith('--font-dpi='):
              try:
                  self.font_dpi_override = int(arg.split('--font-dpi=')[1])
              except:
                  print('[Debug] Invalid Override Font DPI specified. Ignoring.')
                  return
              print('[Debug] Overriding font DPI to ' + str(self.font_dpi_override) + '.')

    def print_verbose(self, feature, text):
        if self.verbose_enabled:
            print('[' + feature + '] ' + text)

    def override_arch(self):
        if not self.simulate_arch == None:
            systemstate.arch = self.simulate_arch

    def override_session(self):
        if not self.simulate_session == None:
            if self.simulate_session == 'vbox':
                systemstate.graphics_vendor = 'VirtualBox'
                systemstate.graphics_grep = 'VirtualBox'
            else:
                systemstate.session_type = self.simulate_session

    def override_codename(self):
        if not self.simulate_codename == None:
            systemstate.codename = self.simulate_codename


if __name__ == "__main__":

    # Process any parameters passed to the program.
    arg = Arguments()

    # Application Initialization
    set_proc_title()
    systemstate = SystemState()
    app = WelcomeApp()
    dynamicapps = DynamicApps()
    preinstallation = PreInstallation()

    # Argument Overrides
    arg.override_arch()
    arg.override_session()
    arg.override_codename()

    print('[Welcome] Application Started.')
    app.run()
