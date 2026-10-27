/** Capture view: live camera, still review, multi-photo flow, file fallback. */

import { PHOTO_ROLES, type PhotoRole } from '../lib/schema.js';
import { intakePhoto } from '../lib/photos.js';
import { CameraController, CameraError, cameraSupport } from './camera.js';
import { button, clear, el, on } from './dom.js';
import { formatBytes } from './format.js';
import { ROLE_LABELS, ensureWorkingObservationId, nextRole } from './journey.js';
import type { AppContext } from './shell.js';

export async function renderCaptureView(
  outlet: HTMLElement,
  ctx: AppContext,
): Promise<(() => void) | void> {
  const camera = new CameraController();
  const objectUrls: string[] = [];
  let stopped = false;
  let workingObservationId: string | null = null;
  let role: PhotoRole = 'front';
  let busy = false;

  const trackUrl = (url: string): string => {
    objectUrls.push(url);
    return url;
  };

  // Adopt an observation the inspector sent for an additional photo.
  const continued = await ctx.repo.getMeta('capture.continue');
  if (continued) {
    const obs = await ctx.repo.getObservation(continued);
    if (obs) {
      workingObservationId = obs.observation_id;
      await ctx.setSelection({ sessionId: obs.session_id, observationId: obs.observation_id });
      role = 'detail';
    }
    await ctx.repo.setMeta('capture.continue', '');
  }

  const session = ctx.selection.sessionId
    ? await ctx.repo.getSession(ctx.selection.sessionId)
    : undefined;

  const root = el('section', { class: 'capture', 'aria-label': 'Capture' });

  if (!session) {
    root.append(await buildSessionGate());
    outlet.append(root);
    return;
  }

  // -- session context ----------------------------------------------------
  const sessionStrip = el('div', { class: 'session-strip' });
  sessionStrip.append(
    el('span', { class: 'session-strip-label' }, 'Session'),
    el('strong', { class: 'session-strip-name' }, session.name),
    (() => {
      const change = button('Change', { kind: 'quiet' });
      on(change, 'click', () => ctx.navigate({ name: 'sessions' }));
      return change;
    })(),
  );
  root.append(sessionStrip);

  /** First-run onboarding and resume gate. A photo needs a session; this is
   *  the only screen between a new operator and the camera. */
  async function buildSessionGate(): Promise<HTMLElement> {
    const sessions = await ctx.repo.listSessions();
    const firstRun = sessions.length === 0;
    const gate = el('section', { class: 'gate', 'aria-label': 'Choose a session' });
    if (firstRun) {
      gate.append(
        el('h1', {}, 'Start capturing'),
        el('p', { class: 'lede' }, 'Photograph each part as you take it apart. Everything stays on this device until you choose to export it.'),
        el('ol', { class: 'steps' },
          el('li', {}, 'Name this teardown below.'),
          el('li', {}, 'Photograph the front, back and details of each part.'),
          el('li', {}, 'Review each part, then export the evidence.')),
      );
    } else {
      gate.append(
        el('h1', {}, 'Continue capturing'),
        el('p', { class: 'lede' }, 'Photos belong to a session. Pick up a teardown, or start a new one.'),
      );
      const select = el('select', { id: 'capture-session', 'aria-label': 'Session' });
      select.append(el('option', { value: '' }, 'Choose a session…'));
      for (const s of sessions) select.append(el('option', { value: s.session_id }, s.name));
      const use = button('Continue', { kind: 'primary' });
      on(use, 'click', async () => {
        if (!select.value) {
          ctx.notice('Choose a session or create one first.', 'error');
          return;
        }
        await ctx.setSelection({ sessionId: select.value });
        ctx.refresh();
      });
      gate.append(el('div', { class: 'field-row' }, select, use));
      gate.append(el('p', { class: 'gate-divider' }, 'Or start a new teardown'));
    }
    const nameInput = el('input', {
      id: 'capture-new-session',
      type: 'text',
      placeholder: firstRun ? 'Session name, e.g. Amplifier teardown' : 'New session name',
      'aria-label': 'New session name',
      maxlength: '120',
    });
    const create = button('Create session', { kind: firstRun ? 'primary' : 'plain' });
    on(create, 'click', async () => {
      const name = nameInput.value.trim();
      if (!name) {
        ctx.notice('Name the session first.', 'error');
        return;
      }
      try {
        const created = await ctx.repo.createSession(name);
        await ctx.setSelection({ sessionId: created.session_id });
        ctx.refresh();
      } catch (error) {
        ctx.notice(error instanceof Error ? error.message : String(error), 'error');
      }
    });
    gate.append(el('div', { class: 'field-row' }, nameInput, create));
    return gate;
  }

  // -- stage + controls ---------------------------------------------------
  // One column on phones (stage first), two columns on wide screens.
  const stageCol = el('div', { class: 'capture-stage' });
  const sideCol = el('div', { class: 'capture-side' });
  root.append(el('div', { class: 'capture-grid' }, stageCol, sideCol));

  // -- role selector ------------------------------------------------------
  const roleGroup = el('div', { class: 'segmented', role: 'group', 'aria-label': 'Photo role' });
  const roleButtons = new Map<PhotoRole, HTMLButtonElement>();
  for (const r of PHOTO_ROLES) {
    const btn = el(
      'button',
      {
        type: 'button',
        class: `segmented-btn${r === role ? ' segmented-active' : ''}`,
        'aria-pressed': r === role ? 'true' : 'false',
      },
      ROLE_LABELS[r],
    );
    on(btn, 'click', () => setRole(r));
    roleButtons.set(r, btn);
    roleGroup.append(btn);
  }
  function setRole(next: PhotoRole): void {
    role = next;
    for (const [r, btn] of roleButtons) {
      const active = r === next;
      btn.classList.toggle('segmented-active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    }
  }
  sideCol.append(roleGroup);

  // -- viewport -----------------------------------------------------------
  const viewport = el('div', { class: 'viewport' });
  const video = el('video', {
    class: 'viewport-video',
    playsinline: 'true',
    muted: 'true',
    'aria-label': 'Live camera preview',
  }) as HTMLVideoElement;
  const grid = el('div', { class: 'overlay-grid', 'aria-hidden': 'true' });
  const reticle = el('div', { class: 'overlay-reticle', 'aria-hidden': 'true' });
  reticle.append(el('span', { class: 'reticle-corner tl' }), el('span', { class: 'reticle-corner tr' }), el('span', { class: 'reticle-corner bl' }), el('span', { class: 'reticle-corner br' }), el('span', { class: 'reticle-dot' }));
  const focusMark = el('span', { class: 'focus-mark', 'aria-hidden': 'true', hidden: 'true' });
  const reviewImg = el('img', { class: 'viewport-review', alt: 'Captured photo preview', hidden: 'true' });
  viewport.append(video, grid, reticle, focusMark, reviewImg);
  stageCol.append(viewport);

  const support = cameraSupport();
  const statusLine = el('p', { class: 'capture-status muted', role: 'status' }, 'Starting camera…');
  sideCol.append(statusLine);

  // -- error / fallback panel ---------------------------------------------
  const fallback = el('div', { class: 'fallback', hidden: 'true' });
  stageCol.append(fallback);

  function showCameraError(error: CameraError): void {
    video.hidden = true;
    grid.hidden = true;
    reticle.hidden = true;
    statusLine.textContent = error.message;
    clear(fallback);
    fallback.hidden = false;
    fallback.append(
      el('h2', {}, 'Live camera unavailable'),
      el('p', {}, error.message),
      el('p', { class: 'muted' }, error.recovery),
      fileInputs(),
    );
    const retry = button('Retry camera', { kind: 'primary' });
    on(retry, 'click', () => void startLive());
    fallback.append(retry);
  }

  function fileInputs(): HTMLElement {
    const wrap = el('div', { class: 'file-row' });
    const cameraInput = el('input', {
      type: 'file',
      accept: 'image/*',
      capture: 'environment',
      hidden: 'true',
      'aria-label': 'Take a photo with the system camera',
    }) as HTMLInputElement;
    const galleryInput = el('input', {
      type: 'file',
      accept: 'image/*',
      multiple: 'true',
      hidden: 'true',
      'aria-label': 'Import photos from gallery or files',
    }) as HTMLInputElement;
    const cameraLabel = el('label', { class: 'btn' }, 'System camera');
    cameraLabel.append(cameraInput);
    const galleryLabel = el('label', { class: 'btn' }, 'Gallery / files');
    galleryLabel.append(galleryInput);
    on(cameraInput, 'change', () => void importFiles(cameraInput.files, 'file', cameraInput));
    on(galleryInput, 'change', () => void importFiles(galleryInput.files, 'gallery', galleryInput));
    wrap.append(cameraLabel, galleryLabel);
    return wrap;
  }

  // Always available, even when the live camera works. Appended last so
  // the shutter row keeps the thumb zone.
  const importSection = el('section', { class: 'import-fallback', 'aria-label': 'Import photos' });
  importSection.append(
    el('h2', {}, 'From files'),
    el('p', { class: 'muted' }, 'System camera or gallery import. Originals are stored unmodified.'),
    fileInputs(),
  );

  // -- shutter row ---------------------------------------------------------
  const controls = el('div', { class: 'shutter-row' });
  const gridToggle = el('button', { type: 'button', class: 'tool', 'aria-pressed': 'true', 'aria-label': 'Toggle grid' }, 'Grid');
  const reticleToggle = el('button', { type: 'button', class: 'tool', 'aria-pressed': 'true', 'aria-label': 'Toggle reticle' }, 'Frame');
  const switchBtn = el('button', { type: 'button', class: 'tool', 'aria-label': 'Switch camera', hidden: 'true' }, 'Flip');
  const torchBtn = el('button', { type: 'button', class: 'tool', 'aria-pressed': 'false', 'aria-label': 'Toggle torch', hidden: 'true' }, 'Light');
  const shutter = el('button', { type: 'button', class: 'shutter', 'aria-label': 'Take photo', disabled: 'true' });
  shutter.append(el('span', { class: 'shutter-inner', 'aria-hidden': 'true' }));
  controls.append(gridToggle, shutter, switchBtn);
  const controlsSecond = el('div', { class: 'tool-row' }, reticleToggle, torchBtn);
  sideCol.append(controls, controlsSecond);

  on(gridToggle, 'click', () => {
    const pressed = gridToggle.getAttribute('aria-pressed') === 'true';
    gridToggle.setAttribute('aria-pressed', pressed ? 'false' : 'true');
    grid.hidden = pressed;
  });
  on(reticleToggle, 'click', () => {
    const pressed = reticleToggle.getAttribute('aria-pressed') === 'true';
    reticleToggle.setAttribute('aria-pressed', pressed ? 'false' : 'true');
    reticle.hidden = pressed;
  });
  let torchOn = false;
  on(torchBtn, 'click', async () => {
    const applied = await camera.setTorch(!torchOn);
    if (applied) {
      torchOn = !torchOn;
      torchBtn.setAttribute('aria-pressed', torchOn ? 'true' : 'false');
    } else {
      torchBtn.hidden = true;
    }
  });
  on(switchBtn, 'click', async () => {
    try {
      statusLine.textContent = 'Switching camera…';
      await camera.switchFacing(video);
      statusLine.textContent = 'Live preview.';
    } catch (error) {
      showCameraError(error instanceof CameraError ? error : new CameraError('failed', 'Camera switch failed.', 'Retry or keep the current camera.'));
    }
  });
  on(video, 'click', async (event) => {
    const rect = video.getBoundingClientRect();
    const pointer = event as MouseEvent;
    const x = (pointer.clientX - rect.left) / rect.width;
    const y = (pointer.clientY - rect.top) / rect.height;
    const applied = await camera.focusAt(x, y);
    focusMark.hidden = false;
    focusMark.style.left = `${x * 100}%`;
    focusMark.style.top = `${y * 100}%`;
    focusMark.classList.toggle('focus-applied', applied);
    window.setTimeout(() => {
      focusMark.hidden = true;
    }, 900);
    if (!applied) statusLine.textContent = 'Tap-to-focus is not supported by this camera.';
  });
  on(shutter, 'click', () => void takePhoto());

  // -- review bar -----------------------------------------------------------
  const reviewBar = el('div', { class: 'review-bar', hidden: 'true' });
  const acceptBtn = button('Accept', { kind: 'primary' });
  const retakeBtn = button('Retake', {});
  reviewBar.append(retakeBtn, acceptBtn);
  sideCol.append(reviewBar);
  let pendingBlob: Blob | null = null;

  on(retakeBtn, 'click', () => {
    pendingBlob = null;
    reviewImg.hidden = true;
    reviewBar.hidden = true;
    video.hidden = false;
    shutter.removeAttribute('disabled');
    statusLine.textContent = 'Live preview.';
  });
  on(acceptBtn, 'click', () => void acceptPhoto('camera'));

  async function takePhoto(): Promise<void> {
    if (busy || !camera.active) return;
    busy = true;
    shutter.setAttribute('disabled', 'true');
    try {
      pendingBlob = await camera.captureStill(video);
      const url = trackUrl(URL.createObjectURL(pendingBlob));
      reviewImg.src = url;
      reviewImg.hidden = false;
      video.hidden = true;
      reviewBar.hidden = false;
      statusLine.textContent = `Review this ${ROLE_LABELS[role].toLowerCase()} photo, then accept or retake.`;
    } catch (error) {
      ctx.notice(error instanceof Error ? error.message : String(error), 'error');
      shutter.removeAttribute('disabled');
    } finally {
      busy = false;
    }
  }

  async function ensureWorkingObservation(source: 'camera' | 'file' | 'gallery'): Promise<string> {
    const resolved = await ensureWorkingObservationId(
      ctx.repo,
      workingObservationId,
      session!.session_id,
      source,
    );
    workingObservationId = resolved.observationId;
    if (resolved.created) await ctx.setSelection({ observationId: resolved.observationId });
    return resolved.observationId;
  }

  async function acceptPhoto(source: 'camera' | 'file' | 'gallery', file?: File): Promise<void> {
    const blob = source === 'camera' ? pendingBlob : (file ?? null);
    if (!blob) return;
    if (busy) return;
    busy = true;
    statusLine.textContent = 'Saving photo…';
    try {
      const observationId = await ensureWorkingObservation(source);
      const result = await intakePhoto(
        ctx.repo,
        observationId,
        blob,
        role,
        source === 'camera' ? `capture-${role}.jpg` : (file?.name ?? 'photo.jpg'),
      );
      pendingBlob = null;
      reviewImg.hidden = true;
      reviewBar.hidden = true;
      video.hidden = camera.active ? false : true;
      if (camera.active) shutter.removeAttribute('disabled');
      setRole(nextRole(role));
      await refreshStrip();
      if (result.duplicates.length > 0) {
        ctx.notice(
          `Saved. Note: identical bytes already exist in ${result.duplicates.length} photo(s); both are kept.`,
          'info',
        );
      } else if (result.previewError) {
        ctx.notice(`Saved (${formatBytes(blob.size)}). ${result.previewError}`, 'info');
      } else {
        statusLine.textContent = `Saved ${formatBytes(blob.size)}. Take the ${ROLE_LABELS[role].toLowerCase()} view, or finish.`;
      }
    } catch (error) {
      ctx.notice(error instanceof Error ? error.message : String(error), 'error');
      statusLine.textContent = 'Save failed. The photo was not stored.';
      if (camera.active && source === 'camera') {
        // Stay in review so the operator can retry the accept.
        reviewBar.hidden = false;
      }
    } finally {
      busy = false;
    }
  }

  async function importFiles(
    files: FileList | null,
    source: 'file' | 'gallery',
    input: HTMLInputElement,
  ): Promise<void> {
    if (!files || files.length === 0) return;
    let saved = 0;
    for (const file of Array.from(files)) {
      try {
        await acceptPhoto(source, file);
        saved++;
      } catch (error) {
        ctx.notice(`${file.name}: ${error instanceof Error ? error.message : String(error)}`, 'error');
      }
    }
    input.value = '';
    if (saved > 0) statusLine.textContent = `Imported ${saved} photo(s).`;
  }

  // -- accepted strip + flow actions ----------------------------------------
  const strip = el('div', { class: 'strip', 'aria-label': 'Accepted photos' });
  sideCol.append(strip);

  async function refreshStrip(): Promise<void> {
    clear(strip);
    for (const url of objectUrls.splice(0)) URL.revokeObjectURL(url);
    if (!workingObservationId) {
      strip.append(el('p', { class: 'muted' }, 'No photos yet for this component.'));
      updateFlowButtons(0);
      return;
    }
    const images = await ctx.repo.listImages(workingObservationId);
    if (images.length === 0) {
      strip.append(el('p', { class: 'muted' }, 'No photos yet for this component.'));
    }
    for (const image of images) {
      const preview = await ctx.repo.getPreview(image.image_id);
      const original = preview ?? (await ctx.repo.getOriginal(image.image_id));
      const figure = el('figure', { class: 'strip-item' });
      if (original) {
        const img = el('img', { alt: `${ROLE_LABELS[image.role]} view`, loading: 'lazy' });
        img.src = trackUrl(URL.createObjectURL(original));
        figure.append(img);
      }
      figure.append(el('figcaption', {}, ROLE_LABELS[image.role]));
      strip.append(figure);
    }
    updateFlowButtons(images.length);
  }

  const flowRow = el('div', { class: 'flow-row' });
  const finishBtn = button('Review component', { kind: 'primary', disabled: 'true' });
  const newBtn = button('New component', {});
  on(finishBtn, 'click', () => {
    if (workingObservationId) ctx.navigate({ name: 'inspect', id: workingObservationId });
  });
  on(newBtn, 'click', async () => {
    workingObservationId = null;
    await ctx.setSelection({ observationId: null });
    setRole('front');
    await refreshStrip();
    statusLine.textContent = 'Started a new component. Take the front photo.';
  });
  flowRow.append(finishBtn, newBtn);
  sideCol.append(flowRow, importSection);

  function updateFlowButtons(count: number): void {
    if (count > 0) finishBtn.removeAttribute('disabled');
    else finishBtn.setAttribute('disabled', 'true');
  }

  async function startLive(): Promise<void> {
    clear(fallback);
    fallback.hidden = true;
    video.hidden = false;
    grid.hidden = gridToggle.getAttribute('aria-pressed') !== 'true';
    reticle.hidden = reticleToggle.getAttribute('aria-pressed') !== 'true';
    statusLine.textContent = 'Starting camera…';
    try {
      await camera.start(video, 'environment');
      if (stopped) return;
      statusLine.textContent = 'Live preview.';
      shutter.removeAttribute('disabled');
      if (camera.canSwitch()) switchBtn.hidden = false;
      const track = camera.videoTrack();
      const caps = track?.getCapabilities?.() as { torch?: boolean } | undefined;
      if (caps?.torch) torchBtn.hidden = false;
    } catch (error) {
      showCameraError(
        error instanceof CameraError
          ? error
          : new CameraError('failed', 'The camera could not be started.', 'Retry or use the file fallback.'),
      );
    }
  }

  if (!support.hasMediaDevices || !support.secure) {
    showCameraError(
      new CameraError(
        support.secure ? 'unsupported' : 'insecure-context',
        support.secure ? 'This browser cannot open a live camera.' : 'Live camera needs a secure context.',
        support.secure
          ? 'Use a current Chrome, Edge, Firefox or Safari, or import photos below.'
          : 'Open Capture over HTTPS or http://localhost, or import photos below.',
      ),
    );
  } else {
    void startLive();
  }
  await refreshStrip();

  outlet.append(root);
  return () => {
    stopped = true;
    camera.stop();
    for (const url of objectUrls) URL.revokeObjectURL(url);
  };
}
