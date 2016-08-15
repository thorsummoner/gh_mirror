""" Fetch all user repos, and ensure all local checkouts are up to date
"""
from __future__ import print_function

import argparse
import logging
import os
import shelve
import subprocess
import sys
import distutils.util

import agithub.GitHub
import git as gitpython
# correct WtfCase
agithub.Github = agithub.GitHub
agithub.Github.Github = agithub.GitHub.GitHub

logging.basicConfig(level=logging.DEBUG)


def _gh_token_git_config():
    """ Fetch github token from git config command
        It is expected the git config option `github.token` is defined
    """
    token = None
    try:
        token = subprocess.check_output(('git', 'config', 'github.token')).strip()
    except subprocess.CalledProcessError as _:
        pass
    return token


GH_TOKEN = next(e for e in [
    os.environ.get('GH_TOKEN', None),
    _gh_token_git_config(),
] if e)

OS_FILE_EXISTS = 17
SHELF_SPACE = os.path.expanduser('~/.cache/gh_mirror.shelf')

DEAFULT_CHECKOUT_DIRECTORY = '/var/lib/git/'

# pylint: disable=line-too-long
ARGP = argparse.ArgumentParser(
    description=__doc__,
    formatter_class=argparse.RawTextHelpFormatter, )
ARGP.add_argument('prefix', nargs='?', help='Only work on repos matching prefix.', default='')
ARGP.add_argument('--renew-cache', '-f', action='store_true', help='Renew cache')
ARGP.add_argument('--checkout-dir', '-d', help='Git checkout directory', default=DEAFULT_CHECKOUT_DIRECTORY)
ARGP.add_argument('--fetch', '-u', type=distutils.util.strtobool, help='Git fetch repo updates', default='true')
# pylint: enable=line-too-long


def _renew_cache(argp, shelf):
    """ Download repo list from github to shelf cache
    """
    gh_api = agithub.Github.Github(token=GH_TOKEN)
    renew_cache = any([
        argp.renew_cache,
        not shelf,
    ])

    if renew_cache:
        logging.info('Renewing Github user repo cache...')
        user_repos = list()
        page = 0
        while True:
            page += 1
            logging.info('Fetching page %s of cache...', page)
            http_code, user_repos_page = gh_api.user.repos.get(page=page)
            if http_code != 200:
                raise UserWarning((http_code, user_repos_page))
            user_repos.extend(user_repos_page)
            if not user_repos_page:
                break
        shelf['user_repos'] = user_repos

def _ensure_directory(argp, repo_fullname):
    """ Ensure we have a directory ready to be checked out into
    """
    ensure_directory = os.path.join(
        argp.checkout_dir,
        os.path.dirname(repo_fullname),
    )
    try:
        os.makedirs(ensure_directory)
    except OSError as err:
        if err.errno != OS_FILE_EXISTS:
            raise err

_global_git_clone_progress_called = False
def _git_clone_progress(_, cur_count, max_count=None, message=''):
    """ Simple printer
    """
    global _global_git_clone_progress_called
    _global_git_clone_progress_called = True
    del _
    percent = '...'
    try:
        percent = int(cur_count) // int(max_count) * 100
    except ValueError as _:
        pass
    sys.stdout.write((
        '\rReceiving objects: {percent:>3}% '
        '({cur_count}/{max_count}), {message}'
        ).format(
            cur_count=cur_count, max_count=max_count, message=message,
            percent=percent
        ))
    sys.stdout.flush()


def _git_checkout(argp, gh_repo):
    """ Init gitpython repo or clone from github
    """
    global _global_git_clone_progress_called
    try:
        gitpython_repo = gitpython.Repo(
            os.path.join(argp.checkout_dir, gh_repo[u'full_name']),
        )
    except gitpython.exc.NoSuchPathError as _:
        print('Cloning {gh_repo[html_url]}'.format(gh_repo=gh_repo))
        _global_git_clone_progress_called = False
        gitpython_repo = gitpython.Repo.clone_from(
            url=gh_repo[u'clone_url'],
            to_path=os.path.join(argp.checkout_dir, gh_repo[u'full_name']),
            progress=_git_clone_progress,
            bare=True,
        )
        if _global_git_clone_progress_called:
            sys.stdout.write('\n')
            sys.stdout.flush()
    return gitpython_repo

def main(argp=None):
    """ Cli entry point
    """
    global _global_git_clone_progress_called

    if argp is None:
        argp = ARGP.parse_args()

    shelf = shelve.open(SHELF_SPACE)

    _renew_cache(argp, shelf)
    user_repos = shelf['user_repos']

    for gh_repo in user_repos:
        if not gh_repo[u'full_name'].startswith(argp.prefix):
            continue
        print(gh_repo[u'full_name'])
        _ensure_directory(argp, gh_repo[u'full_name'])
        gitpython_repo = _git_checkout(argp, gh_repo)

        # For Hosting
        gitpython_repo.daemon_export = True

        # Populate Description
        gitpython_repo.description = gh_repo[u'description']

        if 'cgit_repo_config':
            git_config = gitpython_repo.config_writer()
            if 'with':
                if not git_config.has_section('cgit'):
                    git_config.add_section('cgit')

                git_config.set('cgit', 'defbranch', gh_repo[u'default_branch'])
                git_config.set('cgit', 'desc', gh_repo[u'description'])
                git_config.set('cgit', 'name', gh_repo[u'name'])
                git_config.set('cgit', 'owner', gh_repo[u'owner'][u'login'])

                git_config.set(
                    'cgit', 'clone-url',
                    ' '.join([
                        gh_repo[u'git_url'],
                        gh_repo[u'ssh_url'],
                        gh_repo[u'clone_url'],
                    ])
                )

                if gh_repo[u'homepage']:
                    git_config.set('cgit', 'homepage', gh_repo[u'homepage'])

                if gh_repo[u'default_branch'] == u'gh-pages':
                    git_config.set('cgit', 'enable-html-serving', 'true')

            git_config.release()

        if argp.fetch:
            _global_git_clone_progress_called = False
            updates = gitpython_repo.remote().fetch(
                refspec=[
                    '+refs/heads/*:refs/remotes/origin/*',
                    # '+refs/*:refs/*', ## grrrr bugg
                    # https://github.com/gitpython-developers/GitPython/issues/497
                    '+refs/tags/*:refs/tags/*',
                    '+refs/notes/*:refs/notes/*',
                ],
                progress=_git_clone_progress,
                force=True,
            )
            if _global_git_clone_progress_called:
                sys.stdout.write('\n')
                sys.stdout.flush()

            for fetch_info in updates:
                print('\t{:>20}\t-> {:36}\t{}'.format(
                    fetch_info.ref.name,
                    fetch_info.ref.path,
                    fetch_info.ref.commit.hexsha,
                ))


if __name__ == '__main__':
    main()
