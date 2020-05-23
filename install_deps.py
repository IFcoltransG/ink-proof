#!/usr/bin/env python
# Copyright (C) 2017 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

from collections import namedtuple
from platform import system

from compat import urlretrieve

# The format for the deps below is the following:
# (target_folder, source_url, sha1, target_platform)
# |source_url| can be either a git repo or a http url.
# If a git repo, |sha1| is the committish that will be checked out.
# If a http url, |sha1| is the shasum of the original file.
# If the url is a .zip or .tgz file it will be automatically deflated under
# |target_folder|, taking care of stripping the root folder if it's a single
# root (to avoid ending up with buildtools/protobuf/protobuf-1.2.3/... and have
# instead just buildtools/protobuf).
# |target_platform| is either 'darwin', 'linux' or 'all' and applies the dep
# only on the given platform

DEPS = [
    # Inklecate v0.9.0
    ('deps/inklecate_v0.9.0',
     'https://github.com/inkle/ink/releases/download/0.9.0/inklecate_mac.zip'
     '4c0d45772aea4146699772165e8112fa76ceb295', 'darwin'),
]

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# NODE_MODULES_STATUS_FILE = os.path.join(UI_DIR, 'node_modules', '.last_install')

def ReadFile(path):
  if not os.path.exists(path):
    return None
  with open(path) as f:
    return f.read().strip()


def MkdirRecursive(path):
  # Works with both relative and absolute paths
  cwd = '/' if path.startswith('/') else ROOT_DIR
  for part in path.split('/'):
    cwd = os.path.join(cwd, part)
    if not os.path.exists(cwd):
      os.makedirs(cwd)
    else:
      assert (os.path.isdir(cwd))


def HashLocalFile(path):
  if not os.path.exists(path):
    return None
  with open(path, 'rb') as f:
    return hashlib.sha1(f.read()).hexdigest()


def ExtractZipfilePreservePermissions(zf, info, path):
  zf.extract(info.filename, path=path)
  target_path = os.path.join(path, info.filename)
  min_acls = 0o755 if info.filename.endswith('/') else 0o644
  os.chmod(target_path, (info.external_attr >> 16) | min_acls)


def IsGitRepoCheckoutOutAtRevision(path, revision):
  return ReadFile(os.path.join(path, '.git', 'HEAD')) == revision


def CheckoutGitRepo(path, git_url, revision, check_only):
  if IsGitRepoCheckoutOutAtRevision(path, revision):
    return False
  if check_only:
    return True
  if os.path.exists(path):
    shutil.rmtree(path)
  MkdirRecursive(path)
  logging.info('Fetching %s @ %s into %s', git_url, revision, path)
  subprocess.check_call(['git', 'init', path], cwd=path)
  subprocess.check_call(
      ['git', 'fetch', '--quiet', '--depth', '1', git_url, revision], cwd=path)
  subprocess.check_call(['git', 'checkout', revision, '--quiet'], cwd=path)
  assert (IsGitRepoCheckoutOutAtRevision(path, revision))
  return True


#def InstallNodeModules():
#  logging.info("Running npm install in {0}".format(UI_DIR))
#  subprocess.check_call([os.path.join(UI_DIR, 'npm'), 'install', '--no-save'],
#                        cwd=UI_DIR)
#  with open(NODE_MODULES_STATUS_FILE, 'w') as f:
#    f.write(HashLocalFile(os.path.join(UI_DIR, 'package-lock.json')))


def CheckNodeModules():
  """Returns True if the modules are up-to-date.

  There doesn't seem to be an easy way to check node modules versions. Instead
  just check if package-lock.json changed since the last `npm install` call.
  """
  if not os.path.exists(NODE_MODULES_STATUS_FILE):
    return False
  with open(NODE_MODULES_STATUS_FILE, 'r') as f:
    actual = f.read()
  expected = HashLocalFile(os.path.join(UI_DIR, 'package-lock.json'))
  return expected == actual


def CheckHashes():
  for deps in [BUILD_DEPS_HOST, BUILD_DEPS_ANDROID, TEST_DEPS_ANDROID, UI_DEPS]:
    for rel_path, url, expected_sha1, platform in deps:
      if url.endswith('.git'):
        continue
      logging.info('Downloading %s from %s', rel_path, url)
      with tempfile.NamedTemporaryFile(delete=False) as f:
        f.close()
        urlretrieve(url, f.name)
        actual_sha1 = HashLocalFile(f.name)
        os.unlink(f.name)
        if (actual_sha1 != expected_sha1):
          logging.fatal('SHA1 mismatch for {} expected {} was {}'.format(
              url, expected_sha1, actual_sha1))


def Main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--android', action='store_true')
  parser.add_argument('--ui', action='store_true')
  parser.add_argument('--check-only')
  parser.add_argument('--verify', help='Check all URLs', action='store_true')
  args = parser.parse_args()
  if args.verify:
    CheckHashes()
    return 0
  deps = BUILD_DEPS_HOST
  if args.android:
    deps += BUILD_DEPS_ANDROID + TEST_DEPS_ANDROID
  if args.ui:
    deps += UI_DEPS
  deps_updated = False
  for rel_path, url, expected_sha1, platform in deps:
    if (platform != 'all' and platform != system().lower()):
      continue
    local_path = os.path.join(ROOT_DIR, rel_path)
    if url.endswith('.git'):
      deps_updated |= CheckoutGitRepo(local_path, url, expected_sha1,
                                      args.check_only)
      continue
    is_zip = local_path.endswith('.zip') or local_path.endswith('.tgz')
    zip_target_dir = local_path[:-4] if is_zip else None
    zip_dir_stamp = os.path.join(zip_target_dir, '.stamp') if is_zip else None

    if ((not is_zip and HashLocalFile(local_path) == expected_sha1) or
        (is_zip and ReadFile(zip_dir_stamp) == expected_sha1)):
      continue
    deps_updated = True
    if args.check_only:
      continue
    MkdirRecursive(os.path.dirname(rel_path))
    if HashLocalFile(local_path) != expected_sha1:
      download_path = local_path + '.tmp'
      logging.info('Downloading %s from %s', local_path, url)
      urlretrieve(url, download_path)
      os.chmod(download_path, 0o755)
      actual_sha1 = HashLocalFile(download_path)
      if (actual_sha1 != expected_sha1):
        os.remove(download_path)
        logging.fatal('SHA1 mismatch for {} expected {} was {}'.format(
            download_path, expected_sha1, actual_sha1))
        return 1
      os.rename(download_path, local_path)
    assert (HashLocalFile(local_path) == expected_sha1)

    if is_zip:
      logging.info('Extracting %s into %s' % (local_path, zip_target_dir))
      assert (os.path.commonprefix((ROOT_DIR, zip_target_dir)) == ROOT_DIR)
      if os.path.exists(zip_target_dir):
        logging.info('Deleting stale dir %s' % zip_target_dir)
        shutil.rmtree(zip_target_dir)

      # Decompress the archive.
      if local_path.endswith('.tgz'):
        MkdirRecursive(zip_target_dir)
        subprocess.check_call(['tar', '-xf', local_path], cwd=zip_target_dir)
      elif local_path.endswith('.zip'):
        with zipfile.ZipFile(local_path, 'r') as zf:
          for info in zf.infolist():
            ExtractZipfilePreservePermissions(zf, info, zip_target_dir)

      # If the zip contains one root folder, rebase one level up moving all
      # its sub files and folders inside |target_dir|.
      subdir = os.listdir(zip_target_dir)
      if len(subdir) == 1:
        subdir = os.path.join(zip_target_dir, subdir[0])
        if os.path.isdir(subdir):
          for subf in os.listdir(subdir):
            shutil.move(os.path.join(subdir, subf), zip_target_dir)
          os.rmdir(subdir)

      # Create stamp and remove the archive.
      with open(zip_dir_stamp, 'w') as stamp_file:
        stamp_file.write(expected_sha1)
      os.remove(local_path)

#  if args.ui:
#    # Needs to happen after nodejs is installed above.
#    if args.check_only:
#      deps_updated = not CheckNodeModules()
#    else:
#      InstallNodeModules()

  if args.check_only:
    if not deps_updated:
      with open(args.check_only, 'w') as f:
        f.write('OK')  # The content is irrelevant, just keep GN happy.
      return 0
    argz = ' '.join([x for x in sys.argv[1:] if not '--check-only' in x])
    sys.stderr.write('\033[91mBuild deps are stale. ' +
                     'Please run tools/install-build-deps %s\033[0m' % argz)
    return 1

if __name__ == '__main__':
  logging.basicConfig(level=logging.INFO)
  sys.exit(Main())

