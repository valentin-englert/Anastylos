/** Component inspector: evidence viewer, metadata editor, review state. */

import {
  COMPONENT_CATEGORIES,
  REVIEW_STATES,
  type ComponentObservation,
  type EvidenceImage,
  type MeasurementProvenance,
  type PhotoRole,
} from '../lib/schema.js';
import { describeIdentificationStatus } from '../lib/identify.js';
import { hashToRoute, type AppContext } from './shell.js';
import { button, clear, el, on } from './dom.js';
import { formatBytes, formatDateTime, shortId } from './format.js';

const ROLE_LABELS: Record<PhotoRole, string> = {
  front: 'Front',
  back: 'Back',
  detail: 'Detail',
  other: 'Other',
};

export async function renderInspectorView(
  outlet: HTMLElement,
  ctx: AppContext,
): Promise<(() => void) | void> {
  const urls: string[] = [];
  const route = hashToRoute(window.location.hash);
  const observationId = route.name === 'inspect' ? route.id : null;
  const root = el('section', { class: 'page', 'aria-label': 'Component inspector' });

  if (!observationId) {
    root.append(
      el('h1', {}, 'Inspect'),
      el('p', { class: 'muted' }, 'Nothing selected. Capture a component or pick one from a session.'),
    );
    const row = el('div', { class: 'button-row' });
    const capture = button('Capture', { kind: 'primary' });
    on(capture, 'click', () => ctx.navigate({ name: 'capture' }));
    const sessions = button('Sessions', {});
    on(sessions, 'click', () => ctx.navigate({ name: 'sessions' }));
    row.append(capture, sessions);
    root.append(row);
    outlet.append(root);
    return;
  }

  const obs = await ctx.repo.getObservation(observationId);
  if (!obs) {
    root.append(
      el('h1', {}, 'Component not found'),
      el('p', { class: 'muted' }, 'It may have been deleted or failed validation.'),
    );
    const back = button('Sessions', { kind: 'primary' });
    on(back, 'click', () => ctx.navigate({ name: 'sessions' }));
    root.append(back);
    outlet.append(root);
    return;
  }

  const session = await ctx.repo.getSession(obs.session_id);
  const images = await ctx.repo.listImages(obs.observation_id);

  const title = obs.markings.length > 0 ? obs.markings.map((m) => m.text).join(' · ') : `Component ${shortId(obs.observation_id)}`;
  root.append(el('h1', {}, title));
  root.append(
    el('p', { class: 'muted' },
      `${session ? session.name : '(missing session)'} · captured ${formatDateTime(obs.captured_at)} · via ${obs.capture_source}`),
  );

  async function save(next: ComponentObservation, message: string): Promise<void> {
    try {
      await ctx.repo.saveObservation(next);
      ctx.notice(message);
      ctx.refresh();
    } catch (error) {
      ctx.notice(error instanceof Error ? error.message : String(error), 'error');
    }
  }

  // -- review state ----------------------------------------------------------
  const review = el('section', { class: 'panel', 'aria-label': 'Review state' });
  review.append(el('h2', {}, 'Review'));
  const stepper = el('div', { class: 'segmented', role: 'group', 'aria-label': 'Review state' });
  for (const state of REVIEW_STATES) {
    const btn = el(
      'button',
      {
        type: 'button',
        class: `segmented-btn${obs.review_state === state ? ' segmented-active' : ''}`,
        'aria-pressed': obs.review_state === state ? 'true' : 'false',
      },
      state,
    );
    on(btn, 'click', () => {
      if (obs.review_state !== state) void save({ ...obs, review_state: state }, `Marked ${state}.`);
    });
    stepper.append(btn);
  }
  review.append(stepper);
  const unknowns: string[] = [];
  if (obs.category.state === 'unknown') unknowns.push('category');
  if (obs.quantity.state === 'unknown') unknowns.push('quantity');
  if (obs.markings.length === 0) unknowns.push('markings');
  review.append(
    el('p', { class: 'field-hint' },
      unknowns.length > 0
        ? `Still unknown: ${unknowns.join(', ')}. Unknowns stay unknown — nothing is guessed.`
        : 'Category, quantity and markings are all recorded.'),
  );
  root.append(review);

  // -- photo viewer ------------------------------------------------------------
  const viewer = el('section', { class: 'panel', 'aria-label': 'Photo evidence' });
  viewer.append(el('h2', {}, `Evidence (${images.length})`));
  if (images.length === 0) {
    viewer.append(el('p', { class: 'muted' }, 'No photos. Add one to document this component.'));
  } else {
    let current = images[0]!;
    const frame = el('div', { class: 'crop-frame' });
    const mainImg = el('img', { class: 'viewer-main', alt: 'Original component photograph' });
    const cropBox = el('div', { class: 'crop-box', hidden: 'true' });
    frame.append(mainImg, cropBox);
    const caption = el('p', { class: 'muted viewer-caption' });
    const thumbs = el('div', { class: 'thumbs', role: 'group', 'aria-label': 'Views' });

    async function show(image: EvidenceImage): Promise<void> {
      current = image;
      const original = await ctx.repo.getOriginal(image.image_id);
      if (original) {
        const url = URL.createObjectURL(original);
        urls.push(url);
        mainImg.src = url;
      } else {
        mainImg.removeAttribute('src');
      }
      mainImg.alt = `${ROLE_LABELS[image.role]} view, original photograph`;
      caption.textContent =
        `${ROLE_LABELS[image.role]} · ${image.filename} · ${formatBytes(image.byte_length)} · sha256 ${image.sha256.slice(0, 16)}… · ${formatDateTime(image.captured_at)}`;
      drawCrop(image.crop);
      for (const child of thumbs.querySelectorAll('button')) {
        const active = child.dataset['imageId'] === image.image_id;
        child.classList.toggle('thumb-active', active);
        child.setAttribute('aria-pressed', active ? 'true' : 'false');
      }
    }

    function drawCrop(crop: EvidenceImage['crop']): void {
      if (!crop) {
        cropBox.hidden = true;
        return;
      }
      cropBox.hidden = false;
      cropBox.style.left = `${crop.x * 100}%`;
      cropBox.style.top = `${crop.y * 100}%`;
      cropBox.style.width = `${crop.width * 100}%`;
      cropBox.style.height = `${crop.height * 100}%`;
    }

    for (const image of images) {
      const thumbBtn = el('button', {
        type: 'button',
        class: 'thumb',
        'aria-pressed': 'false',
        'aria-label': `Show ${ROLE_LABELS[image.role]} view`,
      });
      thumbBtn.dataset['imageId'] = image.image_id;
      const preview = await ctx.repo.getPreview(image.image_id);
      const blob = preview ?? (await ctx.repo.getOriginal(image.image_id));
      if (blob) {
        const url = URL.createObjectURL(blob);
        urls.push(url);
        const img = el('img', { alt: '' });
        img.src = url;
        thumbBtn.append(img);
      }
      thumbBtn.append(el('span', { class: 'thumb-label' }, ROLE_LABELS[image.role]));
      on(thumbBtn, 'click', () => void show(image));
      thumbs.append(thumbBtn);
    }

    // Non-destructive crop preview: drag on the frame, save fractions only.
    let dragStart: { x: number; y: number } | null = null;
    let draft: EvidenceImage['crop'] = null;
    const cropHint = el('p', { class: 'field-hint' }, 'Drag on the photo to frame a detail crop. The original is never altered.');
    const cropRow = el('div', { class: 'button-row' });
    const cropSave = button('Save crop preview', { kind: 'primary', disabled: 'true' });
    const cropClear = button('Clear crop', {});
    cropRow.append(cropSave, cropClear);
    const framePoint = (event: PointerEvent): { x: number; y: number } => {
      const rect = frame.getBoundingClientRect();
      return {
        x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
        y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
      };
    };
    on(frame, 'pointerdown', (event) => {
      const pointer = event as PointerEvent;
      frame.setPointerCapture(pointer.pointerId);
      dragStart = framePoint(pointer);
    });
    on(frame, 'pointermove', (event) => {
      if (!dragStart) return;
      const pointer = event as PointerEvent;
      const at = framePoint(pointer);
      draft = {
        x: Math.min(dragStart.x, at.x),
        y: Math.min(dragStart.y, at.y),
        width: Math.abs(at.x - dragStart.x),
        height: Math.abs(at.y - dragStart.y),
      };
      drawCrop(draft);
      if (draft.width > 0.02 && draft.height > 0.02) cropSave.removeAttribute('disabled');
      else cropSave.setAttribute('disabled', 'true');
    });
    on(frame, 'pointerup', () => {
      dragStart = null;
    });
    on(cropSave, 'click', async () => {
      if (!draft || draft.width <= 0 || draft.height <= 0) return;
      try {
        await ctx.repo.updateImage({ ...current, crop: draft });
        ctx.notice('Crop preview saved. The original is untouched.');
        ctx.refresh();
      } catch (error) {
        ctx.notice(error instanceof Error ? error.message : String(error), 'error');
      }
    });
    on(cropClear, 'click', async () => {
      try {
        await ctx.repo.updateImage({ ...current, crop: null });
        ctx.notice('Crop preview cleared.');
        ctx.refresh();
      } catch (error) {
        ctx.notice(error instanceof Error ? error.message : String(error), 'error');
      }
    });

    const photoActions = el('div', { class: 'button-row' });
    const addPhoto = button('Add detail photo', { kind: 'primary' });
    on(addPhoto, 'click', async () => {
      await ctx.repo.setMeta('capture.continue', obs.observation_id);
      await ctx.setSelection({ sessionId: obs.session_id, observationId: obs.observation_id });
      ctx.navigate({ name: 'capture' });
    });
    const removePhoto = button('Remove this photo', { kind: 'danger' });
    on(removePhoto, 'click', async () => {
      if (!window.confirm(`Permanently remove this ${ROLE_LABELS[current.role].toLowerCase()} photo and its bytes?`)) return;
      try {
        await ctx.repo.removeImage(current.image_id);
        ctx.notice('Photo removed.');
        ctx.refresh();
      } catch (error) {
        ctx.notice(error instanceof Error ? error.message : String(error), 'error');
      }
    });
    photoActions.append(addPhoto, removePhoto);

    viewer.append(frame, caption, thumbs, cropHint, cropRow, photoActions);
    await show(current);
  }
  root.append(viewer);

  // -- observed ---------------------------------------------------------------
  const observed = el('section', { class: 'panel', 'aria-label': 'Observed information' });
  observed.append(el('h2', {}, 'Observed'));
  const markingList = el('ul', { class: 'marking-list' });
  if (obs.markings.length === 0) {
    markingList.append(el('li', { class: 'muted' }, 'No markings recorded.'));
  }
  obs.markings.forEach((marking, index) => {
    const item = el('li', { class: 'marking-item' });
    item.append(el('span', {}, marking.text), el('span', { class: 'state state-observed' }, marking.state));
    const remove = button('Remove', { kind: 'quiet', 'aria-label': `Remove marking ${marking.text}` });
    on(remove, 'click', () => {
      const markings = obs.markings.filter((_, i) => i !== index);
      void save({ ...obs, markings }, 'Marking removed.');
    });
    item.append(remove);
    markingList.append(item);
  });
  observed.append(markingList);
  const markingRow = el('div', { class: 'field-row' });
  const markingInput = el('input', {
    type: 'text',
    placeholder: 'Visible marking, e.g. “10k 1%”',
    'aria-label': 'Visible marking',
    maxlength: '200',
  }) as HTMLInputElement;
  const markingAdd = button('Add marking', {});
  on(markingAdd, 'click', () => {
    const text = markingInput.value.trim();
    if (!text) {
      ctx.notice('Type the marking exactly as seen.', 'error');
      return;
    }
    void save({ ...obs, markings: [...obs.markings, { text, state: 'observed' }] }, 'Marking recorded as observed.');
  });
  markingRow.append(markingInput, markingAdd);
  observed.append(markingRow);

  const notesLabel = el('label', { class: 'field' });
  notesLabel.append(el('span', { class: 'field-label' }, 'Operator notes'));
  const notesInput = el('textarea', {
    rows: '3',
    maxlength: '2000',
    placeholder: 'What did you see? Where was it?',
  }) as HTMLTextAreaElement;
  notesInput.value = obs.operator_notes ?? '';
  notesLabel.append(notesInput);
  observed.append(notesLabel);
  const notesSave = button('Save notes', { kind: 'primary' });
  on(notesSave, 'click', () => {
    const value = notesInput.value.trim();
    void save({ ...obs, operator_notes: value || null }, 'Notes saved.');
  });
  observed.append(notesSave);
  root.append(observed);

  // -- confirmed ----------------------------------------------------------------
  const confirmed = el('section', { class: 'panel', 'aria-label': 'Operator-confirmed information' });
  confirmed.append(el('h2', {}, 'Confirmed'));

  const categoryLabel = el('label', { class: 'field' });
  categoryLabel.append(el('span', { class: 'field-label' }, 'Component category'));
  const categorySelect = el('select', {}) as HTMLSelectElement;
  categorySelect.append(el('option', { value: '' }, 'Unknown'));
  for (const category of COMPONENT_CATEGORIES) {
    const option = el('option', { value: category }, category);
    if (obs.category.value === category) option.selected = true;
    categorySelect.append(option);
  }
  if (obs.category.state === 'unknown') (categorySelect.options[0] as HTMLOptionElement).selected = true;
  categoryLabel.append(categorySelect);
  confirmed.append(categoryLabel);
  const categorySave = button('Save category', { kind: 'primary' });
  on(categorySave, 'click', () => {
    const value = categorySelect.value;
    void save(
      { ...obs, category: value ? { value, state: 'confirmed' } : { value: null, state: 'unknown' } },
      value ? `Category confirmed as ${value}.` : 'Category set back to unknown.',
    );
  });
  confirmed.append(el('div', { class: 'button-row' }, categorySave,
    el('span', { class: `state state-${obs.category.state}` }, obs.category.state)));

  confirmed.append(el('h3', {}, 'Measurements'));
  const measureList = el('ul', { class: 'measure-list' });
  if (obs.measurements.length === 0) measureList.append(el('li', { class: 'muted' }, 'None recorded.'));
  obs.measurements.forEach((measurement, index) => {
    const item = el('li', { class: 'measure-item' });
    item.append(
      el('span', {}, `${measurement.label ? `${measurement.label}: ` : ''}${measurement.value} ${measurement.unit}`),
      el('span', { class: 'state state-confirmed' }, measurement.provenance),
    );
    const remove = button('Remove', { kind: 'quiet', 'aria-label': 'Remove measurement' });
    on(remove, 'click', () => {
      const measurements = obs.measurements.filter((_, i) => i !== index);
      void save({ ...obs, measurements }, 'Measurement removed.');
    });
    item.append(remove);
    measureList.append(item);
  });
  confirmed.append(measureList);
  const measureGrid = el('div', { class: 'measure-grid' });
  const measureLabel = el('input', { type: 'text', placeholder: 'Label (optional)', 'aria-label': 'Measurement label', maxlength: '60' }) as HTMLInputElement;
  const measureValue = el('input', { type: 'number', step: 'any', placeholder: 'Value', 'aria-label': 'Measurement value' }) as HTMLInputElement;
  const measureUnit = el('input', { type: 'text', placeholder: 'Unit, e.g. mm', 'aria-label': 'Measurement unit', maxlength: '20' }) as HTMLInputElement;
  const measureProv = el('select', { 'aria-label': 'Measurement provenance' }) as HTMLSelectElement;
  const provOptions: MeasurementProvenance[] = ['operator-measured', 'operator-estimated', 'unknown'];
  for (const prov of provOptions) measureProv.append(el('option', { value: prov }, prov));
  measureGrid.append(measureLabel, measureValue, measureUnit, measureProv);
  confirmed.append(measureGrid);
  const measureAdd = button('Add measurement', {});
  on(measureAdd, 'click', () => {
    const value = Number(measureValue.value);
    const unit = measureUnit.value.trim();
    if (!Number.isFinite(value)) {
      ctx.notice('Enter a numeric measurement value.', 'error');
      return;
    }
    if (!unit) {
      ctx.notice('A measurement needs an explicit unit.', 'error');
      return;
    }
    void save(
      {
        ...obs,
        measurements: [
          ...obs.measurements,
          {
            label: measureLabel.value.trim() || null,
            value,
            unit,
            provenance: measureProv.value as MeasurementProvenance,
          },
        ],
      },
      'Measurement recorded.',
    );
  });
  confirmed.append(measureAdd);

  confirmed.append(el('h3', {}, 'Quantity'));
  confirmed.append(
    el('p', { class: 'field-hint' }, 'Operator-confirmed count only. It is never derived from the number of photos.'),
  );
  const quantityRow = el('div', { class: 'field-row' });
  const quantityInput = el('input', {
    type: 'number',
    min: '1',
    step: '1',
    placeholder: 'Unknown',
    'aria-label': 'Confirmed quantity',
  }) as HTMLInputElement;
  if (obs.quantity.value !== null) quantityInput.value = String(obs.quantity.value);
  const quantitySave = button('Confirm quantity', { kind: 'primary' });
  on(quantitySave, 'click', () => {
    const value = Number(quantityInput.value);
    if (!Number.isInteger(value) || value < 1) {
      ctx.notice('Quantity must be a positive whole number.', 'error');
      return;
    }
    void save({ ...obs, quantity: { value, state: 'confirmed' } }, `Quantity confirmed as ${value}.`);
  });
  const quantityClear = button('Unknown', {});
  on(quantityClear, 'click', () => {
    void save({ ...obs, quantity: { value: null, state: 'unknown' } }, 'Quantity set back to unknown.');
  });
  quantityRow.append(quantityInput, quantitySave, quantityClear);
  confirmed.append(quantityRow);
  root.append(confirmed);

  // -- references ---------------------------------------------------------------
  const refs = el('section', { class: 'panel', 'aria-label': 'References' });
  refs.append(el('h2', {}, 'References'));
  const componentInput = el('input', {
    type: 'text',
    value: obs.component_reference ?? '',
    placeholder: 'Existing component reference (optional)',
    'aria-label': 'Component reference',
    maxlength: '200',
  }) as HTMLInputElement;
  const trayInput = el('input', {
    type: 'text',
    value: obs.tray_reference ?? '',
    placeholder: 'Tray identifier (optional)',
    'aria-label': 'Tray identifier',
    maxlength: '80',
  }) as HTMLInputElement;
  const refsSave = button('Save references', { kind: 'primary' });
  on(refsSave, 'click', () => {
    void save(
      {
        ...obs,
        component_reference: componentInput.value.trim() || null,
        tray_reference: trayInput.value.trim() || null,
      },
      'References saved.',
    );
  });
  refs.append(
    el('label', { class: 'field' }, el('span', { class: 'field-label' }, 'Component reference'), componentInput),
    el('label', { class: 'field' }, el('span', { class: 'field-label' }, 'Tray'), trayInput),
    el('p', { class: 'field-hint' }, 'Manual identifiers only. Capture never invents links to physical trays or assemblies.'),
    refsSave,
  );
  root.append(refs);

  // -- candidates -----------------------------------------------------------------
  const candidates = el('section', { class: 'panel', 'aria-label': 'Identification candidates' });
  candidates.append(el('h2', {}, 'Identification'));
  if (obs.candidates.length === 0) {
    candidates.append(el('p', { class: 'muted' }, describeIdentificationStatus()));
    candidates.append(el('p', { class: 'field-hint' }, 'Status: future functionality. No provider is connected and no photo has left this device.'));
  } else {
    const list = el('ul', { class: 'candidate-list' });
    for (const candidate of obs.candidates) {
      list.append(
        el('li', { class: 'candidate-item' },
          el('span', {}, `#${candidate.rank} ${candidate.label}`),
          el('span', { class: 'state state-suggested' }, `${candidate.state} · ${candidate.provider}/${candidate.model}`)),
      );
    }
    candidates.append(list);
    candidates.append(el('p', { class: 'field-hint' }, 'Suggestions are not identity. Confirm through the fields above.'));
  }
  root.append(candidates);

  // -- export history ---------------------------------------------------------------
  if (obs.export_history.length > 0) {
    const history = el('section', { class: 'panel', 'aria-label': 'Export history' });
    history.append(el('h2', {}, 'Exported in'));
    const list = el('ul', { class: 'history-list' });
    for (const entry of obs.export_history) {
      list.append(el('li', {}, `${shortId(entry.package_id)} · ${formatDateTime(entry.exported_at)}`));
    }
    history.append(list);
    root.append(history);
  }

  // -- danger zone --------------------------------------------------------------------
  const danger = el('section', { class: 'panel panel-danger', 'aria-label': 'Delete component' });
  danger.append(el('h2', {}, 'Delete component'));
  const deleteBtn = button(`Delete component and ${images.length} photo(s)`, { kind: 'danger' });
  on(deleteBtn, 'click', async () => {
    if (!window.confirm(`Delete this component with ALL ${images.length} photo(s)? This cannot be undone.`)) return;
    try {
      await ctx.repo.removeObservation(obs.observation_id);
      if (ctx.selection.observationId === obs.observation_id) {
        await ctx.setSelection({ observationId: null });
      }
      ctx.navigate({ name: 'session', id: obs.session_id });
    } catch (error) {
      ctx.notice(error instanceof Error ? error.message : String(error), 'error');
    }
  });
  danger.append(
    el('p', { class: 'muted' }, 'Deletion is permanent and explicit. Export first if the evidence matters.'),
    deleteBtn,
  );
  root.append(danger);

  outlet.append(root);
  return () => {
    for (const url of urls) URL.revokeObjectURL(url);
    clear(root);
  };
}
