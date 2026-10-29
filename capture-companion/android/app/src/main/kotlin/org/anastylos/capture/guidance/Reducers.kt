package org.anastylos.capture.guidance

// Guided-capture reducers: three orthogonal persisted entities —
// guided session, per-request acquisition, per-attempt delivery —
// plus board registration. Pure functions; the caller persists the
// returned state and performs the physical side effects (shutter,
// upload, dwell reset) exactly when the stated transition commits.
//
// Source: CAMERA-COMPANION-STATE-MACHINE.md entity tables plus the
// FROZEN RESOLUTIONS (three reducers, pause mapping, missing
// transitions, delivery↔receipt mapping). Enum spellings match
// schemas/control.schema.json exactly; see ReducersTest + the
// frozen-vector drift check.

// ---------- Session ----------

/** Wire spelling equals the frozen Session state enum. */
enum class SessionState {
    DISARMED,
    ACTIVE,
    PAUSED,
    EXPIRED,
    CANCELLED,
    ACQUISITION_COMPLETE,
    COMPLETE,
    COMPLETE_WITH_SKIPS,
}

sealed interface SessionEvent {
    data class Arm(val grantLive: Boolean, val permissionGranted: Boolean, val foreground: Boolean) : SessionEvent
    data object OperatorPause : SessionEvent
    data class Resume(val grantLive: Boolean, val registrationFresh: Boolean, val foreground: Boolean) :
        SessionEvent
    data object GrantExpired : SessionEvent
    data object OperatorDisarm : SessionEvent
    data object Backgrounded : SessionEvent
    data object RotationCropChanged : SessionEvent
    data object PermissionLost : SessionEvent
    data object ProcessRestarted : SessionEvent
    data object AcquisitionComplete : SessionEvent
    data object AllQualified : SessionEvent
    data object CompleteWithSkips : SessionEvent
}

data class SessionTransition(val state: SessionState, val accepted: Boolean, val reason: String? = null)

private val SESSION_TERMINAL = setOf(
    SessionState.EXPIRED,
    SessionState.CANCELLED,
    SessionState.COMPLETE,
    SessionState.COMPLETE_WITH_SKIPS,
)

/**
 * Session reducer. Pause retains queue and request but inhibits the
 * shutter; backgrounding, crop-changing rotation, process restart
 * and permission loss disarm (rearm required). Terminal states are
 * immutable.
 */
fun reduceSession(state: SessionState, event: SessionEvent): SessionTransition {
    if (state in SESSION_TERMINAL) {
        return SessionTransition(state, false, "session is terminal")
    }
    return when (event) {
        is SessionEvent.Arm -> {
            if (state != SessionState.DISARMED) {
                SessionTransition(state, false, "arm only from DISARMED")
            } else if (!event.foreground) {
                SessionTransition(state, false, "arm requires foreground")
            } else if (!event.permissionGranted) {
                SessionTransition(state, false, "camera permission required")
            } else if (!event.grantLive) {
                SessionTransition(state, false, "no live grant")
            } else {
                SessionTransition(SessionState.ACTIVE, true)
            }
        }
        SessionEvent.OperatorPause -> {
            if (state != SessionState.ACTIVE) SessionTransition(state, false, "pause only from ACTIVE")
            else SessionTransition(SessionState.PAUSED, true)
        }
        is SessionEvent.Resume -> {
            if (state != SessionState.PAUSED) {
                SessionTransition(state, false, "resume only from PAUSED")
            } else if (!event.foreground) {
                SessionTransition(state, false, "resume requires foreground")
            } else if (!event.grantLive) {
                SessionTransition(state, false, "grant no longer live")
            } else if (!event.registrationFresh) {
                SessionTransition(state, false, "registration not fresh")
            } else {
                SessionTransition(SessionState.ACTIVE, true)
            }
        }
        SessionEvent.GrantExpired -> when (state) {
            SessionState.ACTIVE, SessionState.PAUSED ->
                SessionTransition(SessionState.EXPIRED, true)
            else -> SessionTransition(state, false, "expiry only from ACTIVE or PAUSED")
        }
        SessionEvent.OperatorDisarm -> when (state) {
            SessionState.ACTIVE, SessionState.PAUSED ->
                SessionTransition(SessionState.CANCELLED, true)
            SessionState.DISARMED -> SessionTransition(state, true)
            else -> SessionTransition(state, false, "disarm only from ACTIVE, PAUSED or DISARMED")
        }
        SessionEvent.Backgrounded,
        SessionEvent.RotationCropChanged,
        SessionEvent.PermissionLost,
        SessionEvent.ProcessRestarted,
            -> when (state) {
            SessionState.ACTIVE, SessionState.PAUSED ->
                SessionTransition(SessionState.DISARMED, true)
            SessionState.DISARMED -> SessionTransition(state, true)
            else -> SessionTransition(state, false, "already past arming")
        }
        SessionEvent.AcquisitionComplete -> {
            if (state != SessionState.ACTIVE) {
                SessionTransition(state, false, "acquisition completes only from ACTIVE")
            } else {
                SessionTransition(SessionState.ACQUISITION_COMPLETE, true)
            }
        }
        SessionEvent.AllQualified -> {
            if (state != SessionState.ACQUISITION_COMPLETE) {
                SessionTransition(state, false, "completion needs acquisition-complete first")
            } else {
                SessionTransition(SessionState.COMPLETE, true)
            }
        }
        SessionEvent.CompleteWithSkips -> {
            if (state != SessionState.ACQUISITION_COMPLETE) {
                SessionTransition(state, false, "completion needs acquisition-complete first")
            } else {
                SessionTransition(SessionState.COMPLETE_WITH_SKIPS, true)
            }
        }
    }
}

// ---------- Request ----------

/**
 * Wire spelling equals the frozen Request status enum, and the wire
 * `guidance_state` is always the current status string.
 */
enum class RequestState {
    QUEUED,
    ACCEPTED,
    NAVIGATING,
    ALIGNING,
    HOLD_STILL,
    CAPTURING,
    VERIFYING_IMAGE,
    CAPTURED_LOCAL,
    RETAKE_REQUIRED,
    UNREACHABLE,
    CANCELLED,
    EXPIRED,
    SUPERSEDED,
}

fun RequestState.wireName(): String = name

/** Why an attempt's bytes can never fulfill their request revision. */
enum class LateMark {
    AFTER_CANCEL,
    AFTER_EXPIRE,
    AFTER_SUPERSEDE,
    AFTER_REVOKE,
}

sealed interface RequestEvent {
    data object Accept : RequestEvent
    data object EnterNavigating : RequestEvent
    data object EnterAligning : RequestEvent
    data object GatesPass : RequestEvent
    data object GateFailed : RequestEvent
    data object DwellReady : RequestEvent
    data object JpegPersisted : RequestEvent
    data object VerifyPass : RequestEvent
    data object VerifyFail : RequestEvent
    data class Retry(val attemptsUsed: Int, val autoUsed: Int, val automatic: Boolean) : RequestEvent
    data object CantReach : RequestEvent
    data object Requeued : RequestEvent
    data object Skip : RequestEvent
    data object Cancel : RequestEvent
    data object Expire : RequestEvent
    data object Supersede : RequestEvent
    data object Revoke : RequestEvent
}

data class RequestTransition(
    val state: RequestState,
    val accepted: Boolean,
    val reason: String? = null,
    /** Late mark in force after this transition (sticky once set). */
    val lateMark: LateMark? = null,
    /** True only on the HOLD_STILL→CAPTURING edge: the caller must
     * fire exactly one physical shutter for this transition. */
    val shutterCommitted: Boolean = false,
)

private val REQUEST_TERMINAL = setOf(
    RequestState.CAPTURED_LOCAL,
    RequestState.CANCELLED,
    RequestState.EXPIRED,
    RequestState.SUPERSEDED,
)

private val REQUEST_IN_FLIGHT = setOf(RequestState.CAPTURING, RequestState.VERIFYING_IMAGE)

/** Attempt budget: three attempts total, at most two automatic. */
fun retryAllowed(attemptsUsed: Int, autoUsed: Int, automatic: Boolean): Boolean {
    if (attemptsUsed >= GuidanceHypothesis1.MAX_ATTEMPTS) return false
    if (automatic && autoUsed >= GuidanceHypothesis1.MAX_AUTO_RETAKES) return false
    return true
}

/**
 * Request reducer. Marker loss is NOT a request transition — the
 * registration reducer owns it; the caller maps a non-acquired
 * registration to [RequestEvent.GateFailed]. Cancel/expire/supersede
 * during [RequestState.CAPTURING] or [RequestState.VERIFYING_IMAGE]
 * keep the bytes flowing but mark them late: they are preserved for
 * audit and can never fulfill the request.
 */
fun reduceRequest(
    state: RequestState,
    event: RequestEvent,
    currentLate: LateMark? = null,
): RequestTransition {
    if (state in REQUEST_TERMINAL) {
        return RequestTransition(state, false, "request is terminal", currentLate)
    }
    // A late mark, once set, sticks to every later transition.
    fun stay(mark: LateMark? = currentLate) = RequestTransition(state, true, null, mark)
    fun move(to: RequestState, mark: LateMark? = currentLate, shutter: Boolean = false) =
        RequestTransition(to, true, null, mark, shutter)
    fun reject(reason: String) = RequestTransition(state, false, reason, currentLate)
    if (currentLate != null) {
        // Late bytes keep flowing through capture/verify so they are
        // preserved, but the request can no longer advance to a
        // fulfilled state on this revision, and no transition may
        // bypass the Retry budget gate.
        return when (event) {
            RequestEvent.JpegPersisted ->
                if (state == RequestState.CAPTURING) move(RequestState.VERIFYING_IMAGE)
                else reject("persist only from CAPTURING")
            RequestEvent.VerifyPass, RequestEvent.VerifyFail ->
                if (state == RequestState.VERIFYING_IMAGE) move(RequestState.RETAKE_REQUIRED)
                else reject("verify only from VERIFYING_IMAGE")
            is RequestEvent.Retry -> {
                // Only an operator-cancelled attempt may be retried
                // on the same revision, by a fresh explicit manual
                // action. Expired/superseded revisions and revoked
                // grants cannot take new attempts.
                if (state != RequestState.RETAKE_REQUIRED) {
                    reject("retry only from RETAKE_REQUIRED")
                } else if (currentLate != LateMark.AFTER_CANCEL) {
                    reject("revision cannot take new attempts")
                } else if (event.automatic) {
                    reject("cancelled request retries only manually")
                } else if (!retryAllowed(event.attemptsUsed, event.autoUsed, false)) {
                    reject("attempt budget exhausted")
                } else {
                    move(RequestState.ALIGNING, mark = null)
                }
            }
            RequestEvent.CantReach ->
                if (state in REQUEST_IN_FLIGHT) stay()
                else move(RequestState.UNREACHABLE, mark = null)
            RequestEvent.Skip ->
                if (state in REQUEST_IN_FLIGHT) reject("in-flight attempt finishes first")
                else move(RequestState.CANCELLED, mark = null)
            RequestEvent.Cancel ->
                if (state in REQUEST_IN_FLIGHT) stay()
                else move(RequestState.CANCELLED, mark = null)
            RequestEvent.Expire ->
                if (state in REQUEST_IN_FLIGHT) stay()
                else move(RequestState.EXPIRED, mark = null)
            RequestEvent.Supersede ->
                if (state in REQUEST_IN_FLIGHT) stay()
                else move(RequestState.SUPERSEDED, mark = null)
            RequestEvent.Revoke ->
                if (state in REQUEST_IN_FLIGHT) stay()
                else move(RequestState.CANCELLED, mark = null)
            else -> reject("late attempt cannot advance the request")
        }
    }
    return when (event) {
        RequestEvent.Accept ->
            if (state == RequestState.QUEUED) move(RequestState.ACCEPTED)
            else reject("accept only from QUEUED")
        RequestEvent.EnterNavigating ->
            if (state == RequestState.ACCEPTED || state == RequestState.ALIGNING) {
                move(RequestState.NAVIGATING)
            } else reject("navigate from ACCEPTED or ALIGNING")
        RequestEvent.EnterAligning ->
            if (state == RequestState.ACCEPTED || state == RequestState.NAVIGATING ||
                state == RequestState.HOLD_STILL || state == RequestState.RETAKE_REQUIRED
            ) {
                move(RequestState.ALIGNING)
            } else reject("align from ACCEPTED, NAVIGATING, HOLD_STILL or RETAKE_REQUIRED")
        RequestEvent.GatesPass ->
            if (state == RequestState.ALIGNING) move(RequestState.HOLD_STILL)
            else reject("hold only from ALIGNING")
        RequestEvent.GateFailed -> when (state) {
            RequestState.HOLD_STILL -> move(RequestState.ALIGNING)
            RequestState.ALIGNING, RequestState.NAVIGATING -> stay()
            else -> reject("gate failure outside navigation")
        }
        RequestEvent.DwellReady ->
            if (state == RequestState.HOLD_STILL) move(RequestState.CAPTURING, shutter = true)
            else reject("shutter only from HOLD_STILL")
        RequestEvent.JpegPersisted ->
            if (state == RequestState.CAPTURING) move(RequestState.VERIFYING_IMAGE)
            else reject("persist only from CAPTURING")
        RequestEvent.VerifyPass ->
            if (state == RequestState.VERIFYING_IMAGE) move(RequestState.CAPTURED_LOCAL)
            else reject("verify only from VERIFYING_IMAGE")
        RequestEvent.VerifyFail ->
            if (state == RequestState.VERIFYING_IMAGE) move(RequestState.RETAKE_REQUIRED)
            else reject("verify only from VERIFYING_IMAGE")
        is RequestEvent.Retry -> {
            if (state != RequestState.RETAKE_REQUIRED && state != RequestState.UNREACHABLE) {
                reject("retry only from RETAKE_REQUIRED or UNREACHABLE")
            } else if (!retryAllowed(event.attemptsUsed, event.autoUsed, event.automatic)) {
                reject("attempt budget exhausted")
            } else {
                move(RequestState.ALIGNING)
            }
        }
        RequestEvent.CantReach ->
            if (state in REQUEST_IN_FLIGHT) {
                // "Can't reach" never aborts an in-flight shutter;
                // the operator's pause lands after this attempt.
                stay()
            } else {
                move(RequestState.UNREACHABLE)
            }
        RequestEvent.Requeued ->
            if (state == RequestState.UNREACHABLE) move(RequestState.QUEUED)
            else reject("requeue only from UNREACHABLE")
        // No SKIPPED in the frozen 13-state vocab: a skipped target
        // cancels its request; the omission is recorded at session
        // level (COMPLETE_WITH_SKIPS). A skipped target is never captured.
        RequestEvent.Skip ->
            if (state in REQUEST_IN_FLIGHT) reject("in-flight attempt finishes first")
            else move(RequestState.CANCELLED)
        RequestEvent.Cancel ->
            if (state in REQUEST_IN_FLIGHT) stay(LateMark.AFTER_CANCEL)
            else move(RequestState.CANCELLED)
        RequestEvent.Expire ->
            if (state in REQUEST_IN_FLIGHT) stay(LateMark.AFTER_EXPIRE)
            else move(RequestState.EXPIRED)
        RequestEvent.Supersede ->
            if (state in REQUEST_IN_FLIGHT) stay(LateMark.AFTER_SUPERSEDE)
            else move(RequestState.SUPERSEDED)
        RequestEvent.Revoke ->
            if (state in REQUEST_IN_FLIGHT) stay(LateMark.AFTER_REVOKE)
            else move(RequestState.CANCELLED)
    }
}

// ---------- Attempt delivery ----------

/** Wire spelling equals the frozen AttemptDelivery state enum. */
enum class DeliveryState {
    PENDING_PACKAGE,
    UPLOADING,
    RETRY_WAIT,
    PACKAGE_ACCEPTED,
    SPATIAL_PENDING,
    ACKNOWLEDGED,
    REJECTED,
}

/** Wire spelling equals the frozen Receipt status enum. */
enum class ReceiptKind(val wire: String) {
    PACKAGE_ONLY("package_only"),
    QUALIFIED("qualified"),
    REJECTED("rejected"),
    LATE_AFTER_CANCEL("late_after_cancel"),
    LATE_AFTER_EXPIRE("late_after_expire"),
    LATE_AFTER_SUPERSEDE("late_after_supersede"),
    LATE_AFTER_REVOKE("late_after_revoke"),
}

sealed interface DeliveryEvent {
    data object StartUpload : DeliveryEvent
    data object UploadFailedRetryable : DeliveryEvent
    data object UploadRejected : DeliveryEvent
    data object PackageAccepted : DeliveryEvent
    data object SidecarAssociated : DeliveryEvent
    data object SidecarFailed : DeliveryEvent
    data class ServerReceipt(val kind: ReceiptKind) : DeliveryEvent
}

data class DeliveryTransition(
    val state: DeliveryState,
    val accepted: Boolean,
    val reason: String? = null,
    val lateMark: LateMark? = null,
)

private val DELIVERY_TERMINAL = setOf(DeliveryState.ACKNOWLEDGED, DeliveryState.REJECTED)

private fun ReceiptKind.toLateMark(): LateMark? = when (this) {
    ReceiptKind.LATE_AFTER_CANCEL -> LateMark.AFTER_CANCEL
    ReceiptKind.LATE_AFTER_EXPIRE -> LateMark.AFTER_EXPIRE
    ReceiptKind.LATE_AFTER_SUPERSEDE -> LateMark.AFTER_SUPERSEDE
    ReceiptKind.LATE_AFTER_REVOKE -> LateMark.AFTER_REVOKE
    else -> null
}

/**
 * Delivery reducer. Package acceptance alone is never spatial
 * validation; only a `qualified` receipt acknowledges. Retries reuse
 * the same attempt ID (caller-side); starting an upload while one is
 * in flight is rejected, which is the exactly-once rule at this
 * layer. Late receipts keep the delivery state but mark the attempt
 * so it can never count as qualified.
 */
fun reduceDelivery(
    state: DeliveryState,
    event: DeliveryEvent,
    currentLate: LateMark? = null,
): DeliveryTransition {
    if (state in DELIVERY_TERMINAL) {
        return DeliveryTransition(state, false, "delivery is terminal", currentLate)
    }
    fun stay(mark: LateMark? = currentLate) = DeliveryTransition(state, true, null, mark)
    fun move(to: DeliveryState, mark: LateMark? = currentLate) =
        DeliveryTransition(to, true, null, mark)
    fun reject(reason: String) = DeliveryTransition(state, false, reason, currentLate)
    if (currentLate != null) {
        // A late attempt is audit-only: further receipts collapse
        // idempotently, and no upload edge may advance it.
        if (event is DeliveryEvent.ServerReceipt) return stay()
        return reject("attempt is late; preserved audit-only")
    }
    return when (event) {
        DeliveryEvent.StartUpload -> when (state) {
            DeliveryState.PENDING_PACKAGE, DeliveryState.RETRY_WAIT -> move(DeliveryState.UPLOADING)
            else -> reject("upload already in flight or past uploading")
        }
        DeliveryEvent.UploadFailedRetryable ->
            if (state == DeliveryState.UPLOADING) move(DeliveryState.RETRY_WAIT)
            else reject("retryable failure only from UPLOADING")
        DeliveryEvent.UploadRejected ->
            if (state == DeliveryState.UPLOADING || state == DeliveryState.RETRY_WAIT) {
                move(DeliveryState.REJECTED)
            } else reject("rejection only while uploading")
        DeliveryEvent.PackageAccepted ->
            if (state == DeliveryState.UPLOADING) move(DeliveryState.PACKAGE_ACCEPTED)
            else reject("package acceptance only from UPLOADING")
        DeliveryEvent.SidecarAssociated ->
            if (state == DeliveryState.PACKAGE_ACCEPTED) move(DeliveryState.SPATIAL_PENDING)
            else reject("sidecar association only from PACKAGE_ACCEPTED")
        DeliveryEvent.SidecarFailed ->
            if (state == DeliveryState.PACKAGE_ACCEPTED || state == DeliveryState.SPATIAL_PENDING) {
                move(DeliveryState.REJECTED)
            } else reject("sidecar failure only after package acceptance")
        is DeliveryEvent.ServerReceipt -> when (event.kind) {
            ReceiptKind.QUALIFIED -> when (state) {
                DeliveryState.PACKAGE_ACCEPTED, DeliveryState.SPATIAL_PENDING ->
                    move(DeliveryState.ACKNOWLEDGED)
                else -> reject("qualified receipt only after package acceptance")
            }
            ReceiptKind.PACKAGE_ONLY -> when (state) {
                DeliveryState.PACKAGE_ACCEPTED -> move(DeliveryState.SPATIAL_PENDING)
                DeliveryState.SPATIAL_PENDING -> stay()
                else -> reject("package_only receipt only after package acceptance")
            }
            ReceiptKind.REJECTED -> when (state) {
                DeliveryState.UPLOADING, DeliveryState.RETRY_WAIT,
                DeliveryState.PACKAGE_ACCEPTED, DeliveryState.SPATIAL_PENDING,
                    -> move(DeliveryState.REJECTED)
                else -> reject("rejected receipt only while unresolved")
            }
            ReceiptKind.LATE_AFTER_CANCEL, ReceiptKind.LATE_AFTER_EXPIRE,
            ReceiptKind.LATE_AFTER_SUPERSEDE, ReceiptKind.LATE_AFTER_REVOKE,
                -> when (state) {
                DeliveryState.UPLOADING, DeliveryState.RETRY_WAIT,
                DeliveryState.PACKAGE_ACCEPTED, DeliveryState.SPATIAL_PENDING,
                    -> stay(event.kind.toLateMark())
                else -> reject("late receipt only while unresolved")
            }
        }
    }
}

/** Delivery↔Receipt conformance (frozen mapping 5). */
fun deliveryExpectsReceipt(state: DeliveryState): ReceiptKind? = when (state) {
    DeliveryState.ACKNOWLEDGED -> ReceiptKind.QUALIFIED
    DeliveryState.PACKAGE_ACCEPTED, DeliveryState.SPATIAL_PENDING -> ReceiptKind.PACKAGE_ONLY
    DeliveryState.REJECTED -> ReceiptKind.REJECTED
    else -> null
}

// ---------- Registration ----------

/** Phone registration states (orthogonal to request/delivery). */
enum class RegistrationState {
    UNREGISTERED,
    ACQUIRED,
    DEGRADED,
    LOST,
    BAD_CALIBRATION,
}

/** Wire spelling equals the frozen Status.registration enum. */
enum class WireRegistration(val wire: String) {
    UNREGISTERED("unregistered"),
    VALID("valid"),
    DEGRADED("degraded"),
    AMBIGUOUS("ambiguous"),
    LOST("lost"),
    BAD_CALIBRATION("bad_calibration"),
}

const val AMBIGUOUS_PLANAR_SOLUTION = "ambiguous_planar_solution"

/**
 * Wire→phone registration mapping (frozen resolution 2). Ambiguous
 * planar solutions degrade with an explicit reason and stay
 * ineligible for auto-capture.
 */
fun mapWireRegistration(wire: WireRegistration): Pair<RegistrationState, String?> =
    when (wire) {
        WireRegistration.UNREGISTERED -> RegistrationState.UNREGISTERED to null
        WireRegistration.VALID -> RegistrationState.ACQUIRED to null
        WireRegistration.DEGRADED -> RegistrationState.DEGRADED to "degraded"
        WireRegistration.AMBIGUOUS -> RegistrationState.DEGRADED to AMBIGUOUS_PLANAR_SOLUTION
        WireRegistration.LOST -> RegistrationState.LOST to null
        WireRegistration.BAD_CALIBRATION -> RegistrationState.BAD_CALIBRATION to null
    }

/** Auto-capture is eligible only on a fully acquired registration. */
fun autoCaptureEligible(state: RegistrationState): Boolean = state == RegistrationState.ACQUIRED

sealed interface RegistrationEvent {
    data object PosesValid : RegistrationEvent
    data object PoseAmbiguous : RegistrationEvent
    data object PoseLost : RegistrationEvent
    data object CalibrationInvalid : RegistrationEvent
    data object Reacquired : RegistrationEvent
    data object Reset : RegistrationEvent
}

data class RegistrationTransition(val state: RegistrationState, val accepted: Boolean, val reason: String? = null)

fun reduceRegistration(state: RegistrationState, event: RegistrationEvent): RegistrationTransition {
    fun move(to: RegistrationState) = RegistrationTransition(to, true)
    fun stay() = RegistrationTransition(state, true)
    fun reject(reason: String) = RegistrationTransition(state, false, reason)
    return when (event) {
        RegistrationEvent.PosesValid -> when (state) {
            RegistrationState.UNREGISTERED, RegistrationState.DEGRADED, RegistrationState.LOST ->
                move(RegistrationState.ACQUIRED)
            RegistrationState.ACQUIRED -> stay()
            RegistrationState.BAD_CALIBRATION -> reject("re-profile before acquisition")
        }
        RegistrationEvent.PoseAmbiguous -> when (state) {
            RegistrationState.ACQUIRED, RegistrationState.UNREGISTERED, RegistrationState.LOST ->
                move(RegistrationState.DEGRADED)
            RegistrationState.DEGRADED -> stay()
            RegistrationState.BAD_CALIBRATION -> reject("re-profile before acquisition")
        }
        RegistrationEvent.PoseLost -> when (state) {
            RegistrationState.ACQUIRED, RegistrationState.DEGRADED ->
                move(RegistrationState.LOST)
            RegistrationState.UNREGISTERED, RegistrationState.LOST -> stay()
            RegistrationState.BAD_CALIBRATION -> reject("re-profile before acquisition")
        }
        RegistrationEvent.CalibrationInvalid -> move(RegistrationState.BAD_CALIBRATION)
        RegistrationEvent.Reacquired ->
            if (state == RegistrationState.LOST) move(RegistrationState.ACQUIRED)
            else reject("reacquisition only from LOST")
        RegistrationEvent.Reset -> when (state) {
            RegistrationState.BAD_CALIBRATION, RegistrationState.LOST ->
                move(RegistrationState.UNREGISTERED)
            else -> reject("reset only from LOST or BAD_CALIBRATION")
        }
    }
}
