/** Live camera access with honest capability reporting.
 *
 * Every failure carries recovery instructions for the operator. Still-photo
 * capture prefers `ImageCapture.takePhoto()` (sensor bytes) and falls back
 * to drawing the video frame to canvas.
 */

export type CameraFailure =
  | 'insecure-context'
  | 'unsupported'
  | 'denied'
  | 'missing'
  | 'busy'
  | 'failed';

export class CameraError extends Error {
  readonly kind: CameraFailure;
  readonly recovery: string;

  constructor(kind: CameraFailure, message: string, recovery: string, options?: { cause?: unknown }) {
    super(message);
    this.name = 'CameraError';
    this.kind = kind;
    this.recovery = recovery;
    if (options?.cause !== undefined) (this as { cause?: unknown }).cause = options.cause;
  }
}

export interface CameraSupport {
  secure: boolean;
  hasMediaDevices: boolean;
  hasImageCapture: boolean;
  facingModes: Array<'user' | 'environment'>;
}

export function cameraSupport(): CameraSupport {
  const secure = typeof window === 'undefined' ? false : window.isSecureContext;
  const mediaDevices = typeof navigator !== 'undefined' ? navigator.mediaDevices : undefined;
  return {
    secure,
    hasMediaDevices: !!mediaDevices?.getUserMedia,
    hasImageCapture: typeof ImageCapture !== 'undefined',
    facingModes: ['environment', 'user'],
  };
}

async function countVideoInputs(): Promise<number> {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices.filter((d) => d.kind === 'videoinput').length;
  } catch {
    return 0;
  }
}

function classifyGetUserMediaError(error: unknown): CameraError {
  const name =
    typeof error === 'object' && error !== null && 'name' in error
      ? String((error as { name: unknown }).name)
      : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return new CameraError(
      'denied',
      'Camera permission was denied.',
      'Tap the camera icon in the browser address bar and allow access, then retry. On iOS: Settings → Safari/Chrome → Camera → Allow. Installed PWA: system Settings → Apps → Capture → Camera.',
      { cause: error },
    );
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') {
    return new CameraError(
      'missing',
      'No usable camera was found.',
      'Connect or enable a camera and retry, or use “From files” below to photograph with the system camera app and import the result.',
      { cause: error },
    );
  }
  if (name === 'NotReadableError' || name === 'AbortError') {
    return new CameraError(
      'busy',
      'The camera is busy or could not be started.',
      'Close other apps or tabs using the camera (video calls, scanner apps), then retry. A device restart clears stuck camera locks.',
      { cause: error },
    );
  }
  return new CameraError(
    'failed',
    'The camera could not be started.',
    'Retry, reload the page, or use “From files” below as a fallback.',
    { cause: error },
  );
}

export class CameraController {
  private stream: MediaStream | null = null;
  private facingMode: 'user' | 'environment' = 'environment';
  private videoInputs = 0;

  get active(): boolean {
    return this.stream !== null && this.stream.active;
  }

  get facing(): 'user' | 'environment' {
    return this.facingMode;
  }

  /** Show the camera switcher only when a second camera plausibly exists. */
  canSwitch(): boolean {
    return this.videoInputs >= 2;
  }

  videoTrack(): MediaStreamTrack | null {
    return this.stream?.getVideoTracks()[0] ?? null;
  }

  async start(video: HTMLVideoElement, facing: 'user' | 'environment' = 'environment'): Promise<void> {
    const support = cameraSupport();
    if (!support.secure) {
      throw new CameraError(
        'insecure-context',
        'Live camera needs a secure context.',
        'Open Capture over HTTPS or http://localhost. On a lab LAN, serve with TLS or use “From files” to import photos taken with the system camera.',
      );
    }
    if (!support.hasMediaDevices) {
      throw new CameraError(
        'unsupported',
        'This browser cannot open a live camera.',
        'Use a current Chrome, Edge, Firefox or Safari, or use “From files” below to import photos instead.',
      );
    }
    this.stop();
    this.facingMode = facing;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: facing },
          width: { ideal: 2560 },
          height: { ideal: 1920 },
        },
        audio: false,
      });
    } catch (error) {
      // Some desktop browsers reject `ideal` facingMode; retry unconstrained once.
      if (
        typeof error === 'object' &&
        error !== null &&
        (error as { name?: unknown }).name === 'OverconstrainedError'
      ) {
        try {
          stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        } catch (retryError) {
          throw classifyGetUserMediaError(retryError);
        }
      } else {
        throw classifyGetUserMediaError(error);
      }
    }
    this.stream = stream;
    video.srcObject = stream;
    video.playsInline = true;
    video.muted = true;
    await video.play().catch((error: unknown) => {
      this.stop();
      throw new CameraError('failed', 'The preview could not start.', 'Reload the page and retry.', {
        cause: error,
      });
    });
    this.videoInputs = await countVideoInputs();
  }

  async switchFacing(video: HTMLVideoElement): Promise<'user' | 'environment'> {
    const next = this.facingMode === 'environment' ? 'user' : 'environment';
    await this.start(video, next);
    return next;
  }

  /** Best-effort tap-to-focus. Returns true when the device applied it. */
  async focusAt(x: number, y: number): Promise<boolean> {
    try {
      const track = this.videoTrack();
      if (!track?.getCapabilities) return false;
      // focusMode / pointsOfInterest are platform extensions; read defensively.
      const capabilities = track.getCapabilities() as {
        focusMode?: string[];
        pointsOfInterest?: unknown;
      };
      const modes = capabilities.focusMode ?? [];
      if (!('pointsOfInterest' in capabilities) && !modes.includes('manual')) {
        return false;
      }
      // Coordinates are fractions of the frame (0..1), clamped defensively.
      const point = {
        x: Math.min(1, Math.max(0, x)),
        y: Math.min(1, Math.max(0, y)),
      };
      if (modes.includes('continuous')) {
        await track.applyConstraints({
          advanced: [{ focusMode: 'continuous' } as MediaTrackConstraintSet],
        });
      }
      await track.applyConstraints({
        advanced: [{ pointsOfInterest: [point] } as unknown as MediaTrackConstraintSet],
      });
      return true;
    } catch {
      return false;
    }
  }

  /** Best-effort torch toggle. Returns true when the torch state changed. */
  async setTorch(enabled: boolean): Promise<boolean> {
    try {
      const track = this.videoTrack();
      if (!track?.getCapabilities) return false;
      const capabilities = track.getCapabilities() as { torch?: boolean };
      if (!capabilities.torch) return false;
      await track.applyConstraints({
        advanced: [{ torch: enabled } as unknown as MediaTrackConstraintSet],
      });
      return true;
    } catch {
      return false;
    }
  }

  /** Capture one still photo. Sensor bytes when possible, frame grab otherwise. */
  async captureStill(video: HTMLVideoElement): Promise<Blob> {
    const track = this.videoTrack();
    if (track && typeof ImageCapture !== 'undefined') {
      try {
        const photo = await new ImageCapture(track).takePhoto();
        if (photo && photo.size > 0) return photo;
      } catch {
        // Fall through to the canvas grab.
      }
    }
    const width = video.videoWidth;
    const height = video.videoHeight;
    if (!width || !height) throw new CameraError('failed', 'No video frame is available yet.', 'Wait for the preview, then retry.');
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new CameraError('failed', 'The frame could not be read.', 'Retry, or use “From files”.');
    ctx.drawImage(video, 0, 0, width, height);
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, 'image/jpeg', 0.92),
    );
    if (!blob) throw new CameraError('failed', 'The frame could not be encoded.', 'Retry, or use “From files”.');
    return blob;
  }

  stop(): void {
    if (this.stream) {
      for (const track of this.stream.getTracks()) track.stop();
      this.stream = null;
    }
  }
}
