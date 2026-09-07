import { mkdirSync, writeFileSync } from 'node:fs';
import { KUBECONFIG, CLUSTER, CONTEXT, DEPLOYMENT, NAMESPACE, RELEASE, REPO_DIR, TMP_DIR } from './paths';
import { mustRun, run } from './run';

export function install(): void {
  mustRun('just', ['kind-install'], {
    cwd: REPO_DIR,
    env: { PANDORA_KIND_CLUSTER: CLUSTER },
  });
}

export function exportKubeconfig(): void {
  mkdirSync(TMP_DIR, { recursive: true });
  const args = ['get', 'kubeconfig', '--name', CLUSTER];
  if (process.env.PANDORA_KIND_INTERNAL === '1') {
    args.push('--internal');
  }
  writeFileSync(KUBECONFIG, mustRun('kind', args), { mode: 0o600 });
}

export function refuseAnythingButKind(): void {
  const context = mustRun('kubectl', [
    '--kubeconfig',
    KUBECONFIG,
    'config',
    'current-context',
  ]).trim();
  if (context !== CONTEXT) {
    throw new Error(`refusing E2E context ${context}`);
  }
  const server = mustRun('kubectl', [
    '--kubeconfig',
    KUBECONFIG,
    'config',
    'view',
    '--minify',
    '-o',
    'jsonpath={.clusters[0].cluster.server}',
  ]).trim();
  const host = new URL(server).hostname;
  const allowed = ['127.0.0.1', 'localhost', '0.0.0.0', `${CLUSTER}-control-plane`];
  if (!allowed.includes(host)) {
    throw new Error(`refusing non-Kind server ${server}`);
  }
}

export function kubectl(args: string[]): string {
  return mustRun('kubectl', [
    '--kubeconfig',
    KUBECONFIG,
    '--context',
    CONTEXT,
    '--namespace',
    NAMESPACE,
    ...args,
  ]);
}

export function shell(code: string): string {
  const output = kubectl([
    'exec',
    `deployment/${DEPLOYMENT}`,
    '--',
    'python',
    'manage.py',
    'shell',
    '--no-imports',
    '-c',
    code,
  ]);
  const lines = output.split('\n').map((line) => line.trim()).filter((line) => line !== '');
  const last = lines.at(-1);
  if (last === undefined) {
    return '';
  }
  return last;
}

export function resetApplication(): void {
  shell([
    'from django.contrib.auth import get_user_model',
    'from django.core.management import call_command',
    'from django.db import connection',
    'from pandora.artifacts.models import ArtifactBundle, UploadChunk',
    'from pandora.attachments.models import EventAttachment',
    'from pandora.core.models import DsnKey, IngestToken, Project, TokenScope, TokenSource',
    'from pandora.people.models import Membership, Role, Team',
    'EventAttachment.objects.all().delete()',
    'ArtifactBundle.objects.all().delete()',
    'UploadChunk.objects.all().delete()',
    'with connection.cursor() as cursor:',
    "    cursor.execute('DELETE FROM events_event')",
    "call_command('flush', interactive=False, verbosity=0)",
    "project = Project.objects.create(slug='e2e', name='End to end')",
    "DsnKey.objects.create(project=project, public_key='e2epublickey000000000000000000ff')",
    "token = IngestToken.objects.create(project=project, name='e2e ci', token='e2e-ci-token', source=TokenSource.CI)",
    'token.set_scopes((TokenScope.ARTIFACTS, TokenScope.DEPLOY))',
    'User = get_user_model()',
    "User.objects.create_superuser(username='admin', email='admin@example.test', password='e2e-password')",
    "viewer = User.objects.create_user(username='viewer', password='e2e-password', is_staff=True)",
    "member = User.objects.create_user(username='member', password='e2e-password', is_staff=True)",
    "team = Team.objects.create(name='e2e operators')",
    'Membership.objects.create(user=viewer, team=team, role=Role.VIEWER)',
    'Membership.objects.create(user=member, team=team, role=Role.MEMBER)',
    "print(f'{project.pk}:e2epublickey000000000000000000ff')",
  ].join('\n'));
}

export function recreatePod(): void {
  kubectl(['delete', 'pod', '-l', 'app.kubernetes.io/component=web', '--wait=false']);
  kubectl(['rollout', 'status', `deployment/${DEPLOYMENT}`, '--timeout=5m']);
}

export function upgrade(): void {
  let image = process.env.PANDORA_KIND_IMAGE;
  if (image === undefined) {
    image = 'pandora:kind';
  }
  if (image === '') {
    image = 'pandora:kind';
  }
  const separator = image.lastIndexOf(':');
  let repository = image;
  let tag = 'latest';
  if (separator > 0) {
    repository = image.slice(0, separator);
    tag = image.slice(separator + 1);
  }
  mustRun('helm', [
    'upgrade',
    RELEASE,
    'deploy/helm/pandora',
    '--kubeconfig',
    KUBECONFIG,
    '--kube-context',
    CONTEXT,
    '--namespace',
    NAMESPACE,
    '--wait',
    '--timeout',
    '5m',
    '--set',
    `image.repository=${repository}`,
    '--set',
    `image.tag=${tag}`,
    '--set',
    'image.pullPolicy=Never',
    '--set',
    'host=localhost',
    '--set',
    'persistence.size=1Gi',
    '--set',
    'persistence.storageClass=pandora-kind',
    '--set',
    'settings.secureCookies=false',
    '--set',
    'settings.retentionDays=31',
    '--set',
    'secrets.secretKey=pandora-kind-secret-key-that-is-only-for-tests',
    '--set',
    'superuser.password=pandora-kind-password',
  ], { cwd: REPO_DIR });
}

export function runMaintenanceJobs(): void {
  for (const name of ['monitors', 'prune', 'replay', 'rollouts']) {
    const job = `pandora-e2e-${name}`;
    run('kubectl', [
      '--kubeconfig', KUBECONFIG, '--context', CONTEXT, '--namespace', NAMESPACE,
      'delete', 'job', job, '--ignore-not-found=true',
    ]);
    kubectl(['create', 'job', job, `--from=cronjob/${DEPLOYMENT}-${name}`]);
    kubectl(['wait', '--for=condition=complete', `job/${job}`, '--timeout=5m']);
  }
}
