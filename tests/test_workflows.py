from pathlib import Path
import os
import re
import shutil
import subprocess

import pytest
import yaml


WORKFLOWS = Path('.github/workflows')
PUBLIC_FILES = {'index.html', 'ai_summary.html', 'filtered_feed.xml', 'ai_summary_feed.xml', 'design_verification_feed.xml', 'design_feed.xml', 'verification_feed.xml', 'ai_usage.json'}
SUMMARY_FILES = {'filtered_feed.xml', 'ai_summary_feed.xml', 'ai_summary.html', 'design_verification_feed.xml', 'design_feed.xml', 'verification_feed.xml', 'ai_usage.json', 'state.json'}


def load(name):
    # BaseLoader retains GitHub's `on` keyword instead of YAML 1.1 boolean coercion.
    return yaml.load((WORKFLOWS / name).read_text(encoding='utf-8'), Loader=yaml.BaseLoader)


def steps(workflow):
    return next(iter(workflow['jobs'].values()))['steps']


def step(workflow, name):
    return next(item for item in steps(workflow) if item.get('name') == name)


def array(script, name):
    match = re.search(rf'{name}=\(\s*(.*?)\s*\)', script, re.S)
    assert match is not None
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_workflow_yaml_schedules_and_collection_summary_serialization():
    ci, collect, summary, pages = [load(name) for name in ('ci.yml', 'collect.yml', 'summarize.yml', 'pages.yml')]
    assert collect['on']['schedule'] == [{'cron': '17 */6 * * *'}]
    assert summary['on']['schedule'] == [{'cron': '17 1 * * *'}]
    assert collect['concurrency'] == summary['concurrency']
    assert '${{ github.repository }}' in collect['concurrency']['group']
    assert '${{ github.ref }}' in collect['concurrency']['group']
    assert collect['concurrency']['cancel-in-progress'] == 'false'
    assert {'push', 'pull_request', 'workflow_dispatch'} <= set(ci['on'])
    assert 'python -m pytest -q' in step(ci, 'Run tests')['run']
    assert 'python -m ic_feed.validate' in step(ci, 'Validate publication')['run']
    assert {'workflow_run', 'workflow_dispatch', 'push'} <= set(pages['on'])


def test_collect_commits_only_collection_outputs_after_validation():
    workflow = load('collect.yml')
    publish = step(workflow, 'Commit and push collection outputs')
    assert array(publish['run'], 'allowed_outputs') == {'filtered_feed.xml', 'state.json', 'fetch_failures.tsv'}
    assert 'python -m ic_feed.collect' in step(workflow, 'Collect digital IC literature')['run']
    assert 'python -m ic_feed.validate' in step(workflow, 'Validate collected publication')['run']
    assert "steps.validate.outcome == 'success'" in publish['if']
    assert step(workflow,'Collect digital IC literature')['env']['IEEE_API_KEY'] == '${{ secrets.IEEE_API_KEY }}'


def test_collect_validates_failure_checkpoint_and_propagates_failure():
    workflow = load('collect.yml')
    validate = step(workflow,'Validate collected publication')
    assert "steps.collect.outcome == 'success'" in validate['if']
    assert "steps.collect.outcome == 'failure'" in validate['if']
    publish = step(workflow,'Commit and push collection outputs')
    assert publish['env']['PUBLICATION_OUTCOME'] == '${{ steps.validate.outcome }}'
    failure = step(workflow,'Propagate collection failure')
    assert "steps.collect.outcome == 'failure'" in failure['if'] and failure['run'] == 'exit 1'


def test_summary_secret_boundary_missing_key_skip_and_invalid_publication_usage_only():
    workflow = load('summarize.yml')
    check = step(workflow, 'Check AI configuration')
    assert check['env']['DEEPSEEK_API_KEY'] == '${{ secrets.DEEPSEEK_API_KEY }}'
    assert 'enabled=false' in check['run'] and 'not configured' in check['run']
    assert 'bark=true' in check['run'] and 'bark=false' in check['run']
    generate = step(workflow, 'Generate DeepSeek digest')
    assert "steps.ai.outputs.enabled == 'true'" in generate['if']
    assert generate['continue-on-error'] == 'true'
    assert generate['env']['DEEPSEEK_MODEL'] == "${{ vars.DEEPSEEK_MODEL || 'deepseek-v4-flash-vision-exp' }}"
    assert generate['env']['SEMANTIC_SCHOLAR_API_KEY'] == '${{ secrets.SEMANTIC_SCHOLAR_API_KEY }}'
    assert generate['env']['BARK_ENABLED'] == "${{ secrets.BARK_TOKEN != '' && vars.BARK_ENABLED != 'false' }}"
    assert '${{ runner.temp }}/ic-notification.json' in generate['run']
    validate = step(workflow, 'Validate generated publication')
    assert "steps.summarize.outcome == 'success'" in validate['if']
    assert "steps.summarize.outcome == 'failure'" in validate['if']
    publish = step(workflow, 'Commit and push summary outputs')
    assert 'allowed_outputs=("ai_usage.json")' in publish['run']
    assert array(publish['run'], 'successful_outputs') == SUMMARY_FILES
    assert 'ready=true' in publish['run']
    assert "steps.validate.outcome == 'success'" in publish['if']
    assert 'PUBLICATION_OUTCOME' in publish['run']
    assert '--autostash' in publish['run']
    assert 'git add .' not in publish['run'] and 'git add -A' not in publish['run']


def test_summary_notifies_after_validated_publication_including_no_diff():
    workflow = load('summarize.yml')
    notify = step(workflow, 'Send Bark notifications')
    assert "steps.summarize.outcome == 'success'" in notify['if']
    assert "steps.validate.outcome == 'success'" in notify['if']
    assert "steps.publish.outputs.ready == 'true'" in notify['if']
    assert "steps.ai.outputs.bark == 'true'" in notify['if']
    assert '${{ runner.temp }}/ic-notification.json' in notify['run']
    assert notify['env']['BARK_TOKEN'] == '${{ secrets.BARK_TOKEN }}'
    assert notify['continue-on-error'] == 'true'
    failure = step(workflow, 'Propagate summary failure')
    assert "steps.summarize.outcome == 'failure'" in failure['if']
    assert "steps.validate.outcome == 'failure'" in failure['if']


def test_pages_deploys_current_main_and_only_public_files():
    workflow = load('pages.yml')
    job = workflow['jobs']['deploy']
    assert 'if' not in job
    assert 'python -m ic_feed.validate' in step(workflow, 'Validate publication before deployment')['run']
    assert workflow['on']['workflow_run']['workflows'] == ['Collect digital IC literature', 'Summarize digital IC literature']
    assert step(workflow, 'Check out current main')['with']['ref'] == 'main'
    assert workflow['permissions'] == {'contents': 'read', 'pages': 'write', 'id-token': 'write'}
    stage = step(workflow, 'Stage public site')['run']
    assert array(stage, 'public_files') == PUBLIC_FILES
    assert 'assets/.' in stage
    assert 'state.json' not in stage and 'src/' not in stage
    assert step(workflow, 'Upload Pages artifact')['with']['path'] == '_site'
    uses = {item.get('uses') for item in steps(workflow)}
    assert {'actions/checkout@v6', 'actions/setup-python@v6', 'actions/configure-pages@v5', 'actions/upload-pages-artifact@v4', 'actions/deploy-pages@v4'} <= uses


@pytest.fixture
def git_publication_repo(tmp_path):
    git = shutil.which('git')
    if not git:
        pytest.skip('git is required for the isolated publication script test')
    bash = str(Path(git).parent.parent / 'bin' / 'bash.exe') if os.name == 'nt' else shutil.which('bash')
    if not bash or not Path(bash).exists():
        pytest.skip('bash is required for the isolated publication script test')
    remote = tmp_path / 'origin.git'
    repo = tmp_path / 'repo'
    repo.mkdir()
    def command(*args, cwd=repo):
        return subprocess.run([git, *args], cwd=cwd, check=True, capture_output=True, text=True)
    command('init', '--bare', '--initial-branch=main', str(remote))
    command('init', '--initial-branch=main')
    command('config', 'user.name', 'Workflow Test')
    command('config', 'user.email', 'workflow-test@example.invalid')
    for name in sorted(SUMMARY_FILES | {'README.md'}):
        (repo / name).write_text('initial content\n', encoding='utf-8')
    command('add', '--', *sorted(SUMMARY_FILES | {'README.md'}))
    command('commit', '-m', 'test fixture')
    command('remote', 'add', 'origin', str(remote))
    command('push', '-u', 'origin', 'main')
    return repo, bash, command


def run_summary_publish(repo, bash, tmp_path, outcome, validation='success'):
    output = tmp_path / 'github-output.txt'
    script = step(load('summarize.yml'), 'Commit and push summary outputs')['run']
    env = {**os.environ, 'GITHUB_OUTPUT': output.as_posix(), 'GITHUB_REF_NAME': 'main', 'SUMMARY_OUTCOME': outcome, 'PUBLICATION_OUTCOME': validation}
    subprocess.run([bash, '-c', script], cwd=repo, env=env, check=True, capture_output=True, text=True)
    return output.read_text(encoding='utf-8')


def test_invalid_publication_pushes_usage_and_preserves_rejected_outputs(git_publication_repo, tmp_path):
    repo, bash, command = git_publication_repo
    (repo / 'ai_usage.json').write_text('new recorded usage\n', encoding='utf-8')
    (repo / 'state.json').write_text('partial generated transaction\n', encoding='utf-8')
    (repo / 'ai_summary_feed.xml').write_text('partial generated RSS\n', encoding='utf-8')
    output = run_summary_publish(repo, bash, tmp_path, 'failure', 'failure')
    assert 'ready=true' not in output
    assert command('show', 'origin/main:ai_usage.json').stdout == 'new recorded usage\n'
    assert command('show', 'origin/main:state.json').stdout == 'initial content\n'
    assert (repo / 'state.json').read_text(encoding='utf-8') == 'partial generated transaction\n'
    assert (repo / 'ai_summary_feed.xml').read_text(encoding='utf-8') == 'partial generated RSS\n'


def test_partial_ai_failure_still_pushes_valid_completed_batches(git_publication_repo, tmp_path):
    repo, bash, command = git_publication_repo
    (repo / 'ai_usage.json').write_text('partial recorded usage\n', encoding='utf-8')
    (repo / 'state.json').write_text('completed valid batches\n', encoding='utf-8')
    (repo / 'design_feed.xml').write_text('valid partial design RSS\n', encoding='utf-8')
    assert 'ready=true' in run_summary_publish(repo, bash, tmp_path, 'failure', 'success')
    assert command('show', 'origin/main:state.json').stdout == 'completed valid batches\n'
    assert command('show', 'origin/main:design_feed.xml').stdout == 'valid partial design RSS\n'


def test_successful_summary_pushes_whitelist_and_preserves_unrelated_edits(git_publication_repo, tmp_path):
    repo, bash, command = git_publication_repo
    (repo / 'ai_usage.json').write_text('new recorded usage\n', encoding='utf-8')
    (repo / 'design_feed.xml').write_text('new design RSS\n', encoding='utf-8')
    (repo / 'README.md').write_text('unrelated local edit\n', encoding='utf-8')
    assert 'ready=true' in run_summary_publish(repo, bash, tmp_path, 'success')
    assert command('show', 'origin/main:design_feed.xml').stdout == 'new design RSS\n'
    assert command('show', 'origin/main:README.md').stdout == 'initial content\n'
    assert (repo / 'README.md').read_text(encoding='utf-8') == 'unrelated local edit\n'


def test_unchanged_successful_summary_is_ready_for_no_recommendation_notification(git_publication_repo, tmp_path):
    repo, bash, command = git_publication_repo
    before = command('rev-parse', 'HEAD').stdout
    assert 'ready=true' in run_summary_publish(repo, bash, tmp_path, 'success')
    assert command('rev-parse', 'HEAD').stdout == before


@pytest.mark.parametrize('validation', ['success','failure','skipped'])
def test_failed_collection_only_publishes_validated_checkpoint_and_failure_log(git_publication_repo, validation):
    repo, bash, command = git_publication_repo
    (repo/'state.json').write_text('new frozen checkpoint\n',encoding='utf-8')
    (repo/'filtered_feed.xml').write_text('unvalidated feed mutation\n',encoding='utf-8')
    (repo/'fetch_failures.tsv').write_text('logged source failure\n',encoding='utf-8')
    script = step(load('collect.yml'),'Commit and push collection outputs')['run']
    env = {**os.environ,'GITHUB_REF_NAME':'main','COLLECTION_OUTCOME':'failure','PUBLICATION_OUTCOME':validation}
    subprocess.run([bash,'-c',script],cwd=repo,env=env,check=True,capture_output=True,text=True)
    expected = 'new frozen checkpoint\n' if validation == 'success' else 'initial content\n'
    assert command('show','origin/main:state.json').stdout == expected
    assert command('show','origin/main:filtered_feed.xml').stdout == 'initial content\n'
    assert command('show','origin/main:fetch_failures.tsv').stdout == 'logged source failure\n'
