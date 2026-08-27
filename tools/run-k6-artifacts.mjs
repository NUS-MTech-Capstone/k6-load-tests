#!/usr/bin/env node
import { createWriteStream, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';

const profiles = {
  smoke: 'scripts/smoke.js',
  load: 'scripts/session_lifecycle.js',
  stress: 'scripts/stress_session_start.js',
  agentic: 'scripts/agentic_ai_reads.js',
  audit: 'scripts/audit_reads.js',
};

function usage() {
  const names = Object.keys(profiles).join('|');
  return `Usage: node tools/run-k6-artifacts.mjs <${names}> [--dry-run]

Creates results/<profile>/<timestamp>/ with:
  - report.html
  - summary.json
  - k6.log
  - before.png
  - after.png
  - run.mov when K6_CAPTURE_VIDEO=1
`;
}

function timestamp() {
  return new Date().toISOString().replace(/[:.]/g, '-');
}

function envEnabled(name) {
  return ['1', 'true', 'yes', 'on'].includes(String(process.env[name] || '').toLowerCase());
}

function boundedSeconds(name, fallback) {
  const parsed = Number(process.env[name] || fallback);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback;
  }
  return Math.min(Math.ceil(parsed), 3600);
}

function writeLog(log, message) {
  log.write(message.endsWith('\n') ? message : `${message}\n`);
}

function captureScreenshot(path, label, log) {
  if (process.platform !== 'darwin') {
    writeLog(log, `Skipping ${label} screenshot: screencapture is only supported by this wrapper on macOS.`);
    return;
  }

  const result = spawnSync('screencapture', ['-x', '-m', path], {
    encoding: 'utf8',
  });
  if (result.status === 0) {
    writeLog(log, `Captured ${label} screenshot: ${path}`);
    return;
  }

  writeLog(log, `Unable to capture ${label} screenshot.`);
  if (result.stderr) {
    writeLog(log, result.stderr);
  }
}

function startVideo(path, log) {
  if (!envEnabled('K6_CAPTURE_VIDEO')) {
    return null;
  }
  if (process.platform !== 'darwin') {
    writeLog(log, 'Skipping video capture: screencapture is only supported by this wrapper on macOS.');
    return null;
  }

  const seconds = boundedSeconds('K6_VIDEO_SECONDS', 180);
  writeLog(log, `Starting screen recording for up to ${seconds}s: ${path}`);
  const child = spawn('screencapture', ['-x', '-m', '-v', '-V', String(seconds), path], {
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  child.stderr.on('data', (chunk) => log.write(chunk));
  const done = new Promise((resolveDone) => {
    child.on('error', (error) => {
      writeLog(log, `Unable to start video capture: ${error.message}`);
      resolveDone();
    });
    child.on('close', (code) => {
      writeLog(log, `Screen recording finished with exit code ${code}.`);
      resolveDone();
    });
  });
  return { done };
}

async function waitForVideo(recording) {
  if (!recording) {
    return;
  }
  await recording.done;
}

async function runK6(script, artifactDir, profile, log) {
  const k6 = process.env.K6 || 'k6';
  const env = {
    ...process.env,
    K6_ARTIFACT_DIR: artifactDir,
    K6_REPORT_NAME: profile,
  };

  writeLog(log, `Running ${k6} run ${script}`);
  const child = spawn(k6, ['run', script], {
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  child.stdout.on('data', (chunk) => {
    process.stdout.write(chunk);
    log.write(chunk);
  });
  child.stderr.on('data', (chunk) => {
    process.stderr.write(chunk);
    log.write(chunk);
  });

  return new Promise((resolveRun) => {
    child.on('error', (error) => {
      writeLog(log, `Unable to start k6: ${error.message}`);
      resolveRun(127);
    });
    child.on('close', (code) => resolveRun(code ?? 1));
  });
}

async function main() {
  const args = process.argv.slice(2);
  if (args.includes('--help') || args.includes('-h')) {
    console.log(usage());
    return 0;
  }

  const profile = args.find((arg) => !arg.startsWith('-'));
  if (!profile || !profiles[profile]) {
    console.error(usage());
    return 2;
  }

  const artifactDir = resolve('results', profile, timestamp());
  const script = profiles[profile];
  const dryRun = args.includes('--dry-run');

  if (dryRun) {
    console.log(`Profile: ${profile}`);
    console.log(`Script: ${script}`);
    console.log(`Artifact directory: ${artifactDir}`);
    console.log(`Video: ${envEnabled('K6_CAPTURE_VIDEO') ? 'enabled' : 'disabled'}`);
    return 0;
  }

  mkdirSync(artifactDir, { recursive: true });
  const log = createWriteStream(resolve(artifactDir, 'k6.log'), { flags: 'a' });
  writeLog(log, `Artifact directory: ${artifactDir}`);

  captureScreenshot(resolve(artifactDir, 'before.png'), 'before', log);
  const recording = startVideo(resolve(artifactDir, 'run.mov'), log);
  if (recording) {
    await sleep(1000);
  }

  const exitCode = await runK6(script, artifactDir, profile, log);
  captureScreenshot(resolve(artifactDir, 'after.png'), 'after', log);
  await waitForVideo(recording);

  writeLog(log, `Artifacts written to ${artifactDir}`);
  log.end();
  console.log(`Artifacts written to ${artifactDir}`);
  return exitCode;
}

main()
  .then((code) => {
    process.exitCode = code;
  })
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
