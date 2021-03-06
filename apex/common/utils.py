##############################################################################
# Copyright (c) 2016 Tim Rozet (trozet@redhat.com) and others.
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Apache License, Version 2.0
# which accompanies this distribution, and is available at
# http://www.apache.org/licenses/LICENSE-2.0
##############################################################################

import datetime
import json
import logging
import os
import pprint
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import urllib.parse
import yaml


def str2bool(var):
    if isinstance(var, bool):
        return var
    else:
        return var.lower() in ("true", "yes")


def parse_yaml(yaml_file):
    with open(yaml_file) as f:
        parsed_dict = yaml.safe_load(f)
        return parsed_dict


def dump_yaml(data, file):
    """
    Dumps data to a file as yaml
    :param data: yaml to be written to file
    :param file: filename to write to
    :return:
    """
    logging.debug("Writing file {} with "
                  "yaml data:\n{}".format(file, yaml.safe_dump(data)))
    with open(file, "w") as fh:
        yaml.safe_dump(data, fh, default_flow_style=False)


def dict_objects_to_str(dictionary):
        if isinstance(dictionary, list):
            tmp_list = []
            for element in dictionary:
                if isinstance(element, dict):
                    tmp_list.append(dict_objects_to_str(element))
                else:
                    tmp_list.append(str(element))
            return tmp_list
        elif not isinstance(dictionary, dict):
            if not isinstance(dictionary, bool):
                return str(dictionary)
            else:
                return dictionary
        return dict((k, dict_objects_to_str(v)) for
                    k, v in dictionary.items())


def run_ansible(ansible_vars, playbook, host='localhost', user='root',
                tmp_dir=None, dry_run=False):
    """
    Executes ansible playbook and checks for errors
    :param ansible_vars: dictionary of variables to inject into ansible run
    :param playbook: playbook to execute
    :param tmp_dir: temp directory to store ansible command
    :param dry_run: Do not actually apply changes
    :return: None
    """
    logging.info("Executing ansible playbook: {}".format(playbook))
    inv_host = "{},".format(host)
    if host == 'localhost':
        conn_type = 'local'
    else:
        conn_type = 'smart'
    ansible_command = ['ansible-playbook', '--become', '-i', inv_host,
                       '-u', user, '-c', conn_type, '-T', '30',
                       playbook, '-vv']
    if dry_run:
        ansible_command.append('--check')

    if isinstance(ansible_vars, dict) and ansible_vars:
        logging.debug("Ansible variables to be set:\n{}".format(
            pprint.pformat(ansible_vars)))
        ansible_command.append('--extra-vars')
        ansible_command.append(json.dumps(ansible_vars))
        if tmp_dir:
            ansible_tmp = os.path.join(tmp_dir,
                                       os.path.basename(playbook) + '.rerun')
            # FIXME(trozet): extra vars are printed without single quotes
            # so a dev has to add them manually to the command to rerun
            # the playbook.  Need to test if we can just add the single quotes
            # to the json dumps to the ansible command and see if that works
            with open(ansible_tmp, 'w') as fh:
                fh.write("ANSIBLE_HOST_KEY_CHECKING=FALSE {}".format(
                    ' '.join(ansible_command)))

    my_env = os.environ.copy()
    my_env['ANSIBLE_HOST_KEY_CHECKING'] = 'False'
    logging.info("Executing playbook...this may take some time")
    p = subprocess.Popen(ansible_command,
                         stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE,
                         bufsize=1,
                         env=my_env,
                         universal_newlines=True)
    # read first line
    x = p.stdout.readline()
    # initialize task
    task = ''
    while x:
        # append lines to task
        task += x
        # log the line and read another
        x = p.stdout.readline()
        # deliver the task to info when we get a blank line
        if not x.strip():
            task += x
            logging.info(task.replace('\\n', '\n'))
            task = ''
            x = p.stdout.readline()
    # clean up and get return code
    p.stdout.close()
    rc = p.wait()
    if rc:
        # raise errors
        e = "Ansible playbook failed. See Ansible logs for details."
        logging.error(e)
        raise Exception(e)


def fetch_upstream_and_unpack(dest, url, targets):
    """
    Fetches targets from a url destination and downloads them if they are
    newer.  Also unpacks tar files in dest dir.
    :param dest: Directory to download and unpack files to
    :param url: URL where target files are located
    :param targets: List of target files to download
    :return: None
    """
    os.makedirs(dest, exist_ok=True)
    assert isinstance(targets, list)
    for target in targets:
        download_target = True
        target_url = urllib.parse.urljoin(url, target)
        target_dest = os.path.join(dest, target)
        logging.debug("Fetching and comparing upstream target: \n{}".format(
            target_url))
        try:
            u = urllib.request.urlopen(target_url)
        except urllib.error.URLError as e:
            logging.error("Failed to fetch target url. Error: {}".format(
                e.reason))
            raise
        if os.path.isfile(target_dest):
            logging.debug("Previous file found: {}".format(target_dest))
            metadata = u.info()
            headers = metadata.items()
            target_url_date = None
            for header in headers:
                if isinstance(header, tuple) and len(header) == 2:
                    if header[0] == 'Last-Modified':
                        target_url_date = header[1]
                        break
            if target_url_date is not None:
                target_dest_mtime = os.path.getmtime(target_dest)
                target_url_mtime = time.mktime(
                    datetime.datetime.strptime(target_url_date,
                                               "%a, %d %b %Y %X "
                                               "GMT").timetuple())
                if target_url_mtime > target_dest_mtime:
                    logging.debug('URL target is newer than disk...will '
                                  'download')
                else:
                    logging.info('URL target does not need to be downloaded')
                    download_target = False
            else:
                logging.debug('Unable to find last modified url date')
        if download_target:
            urllib.request.urlretrieve(target_url, filename=target_dest)
            logging.info("Target downloaded: {}".format(target))
        if target.endswith('.tar'):
            logging.info('Unpacking tar file')
            tar = tarfile.open(target_dest)
            tar.extractall(path=dest)
            tar.close()
