package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Orthogonal reducer behavior. Cases tagged `control-transitions`
 * implement the phone-side expectations of
 * `capture-companion/control/contracts/vectors/control-transitions.json`
 * by vector id (contract ec313f7f; ids verified against the frozen
 * file in the Wave-1 drift check). Enum spellings below mirror
 * schemas/control.schema.json exactly.
 */
class ReducersTest {
    // ---------- Session ----------

    @Test
    fun armNeedsForegroundPermissionAndGrant() {
        val ok = SessionEvent.Arm(grantLive = true, permissionGranted = true, foreground = true)
        assertThat(reduceSession(SessionState.DISARMED, ok))
            .isEqualTo(SessionTransition(SessionState.ACTIVE, true))
        assertThat(
            reduceSession(SessionState.DISARMED, ok.copy(grantLive = false)).accepted,
        ).isFalse()
        assertThat(
            reduceSession(SessionState.DISARMED, ok.copy(permissionGranted = false)).accepted,
        ).isFalse()
        assertThat(
            reduceSession(SessionState.DISARMED, ok.copy(foreground = false)).accepted,
        ).isFalse()
    }

    @Test
    fun pauseRetainsSessionAndResumeNeedsForegroundGrantAndFreshPose() {
        // control-transitions: pause-retains-queue.
        val paused = reduceSession(SessionState.ACTIVE, SessionEvent.OperatorPause)
        assertThat(paused).isEqualTo(SessionTransition(SessionState.PAUSED, true))
        val resume = SessionEvent.Resume(grantLive = true, registrationFresh = true, foreground = true)
        assertThat(reduceSession(SessionState.PAUSED, resume))
            .isEqualTo(SessionTransition(SessionState.ACTIVE, true))
        assertThat(
            reduceSession(SessionState.PAUSED, resume.copy(foreground = false)).accepted,
        ).isFalse()
        assertThat(
            reduceSession(SessionState.PAUSED, resume.copy(grantLive = false)).accepted,
        ).isFalse()
        assertThat(
            reduceSession(SessionState.PAUSED, resume.copy(registrationFresh = false)).accepted,
        ).isFalse()
    }

    @Test
    fun expiryFromActiveOrPaused() {
        // control-transitions: pause-retains-queue (grant/lease expiry).
        assertThat(reduceSession(SessionState.PAUSED, SessionEvent.GrantExpired))
            .isEqualTo(SessionTransition(SessionState.EXPIRED, true))
        assertThat(reduceSession(SessionState.ACTIVE, SessionEvent.GrantExpired))
            .isEqualTo(SessionTransition(SessionState.EXPIRED, true))
    }

    @Test
    fun disarmCancelsActiveOrPaused() {
        assertThat(reduceSession(SessionState.ACTIVE, SessionEvent.OperatorDisarm))
            .isEqualTo(SessionTransition(SessionState.CANCELLED, true))
        assertThat(reduceSession(SessionState.PAUSED, SessionEvent.OperatorDisarm))
            .isEqualTo(SessionTransition(SessionState.CANCELLED, true))
        // Disarm is always safe, even when already disarmed.
        assertThat(reduceSession(SessionState.DISARMED, SessionEvent.OperatorDisarm))
            .isEqualTo(SessionTransition(SessionState.DISARMED, true))
    }

    @Test
    fun backgroundRotationDeathAndPermissionLossDisarm() {
        // control-transitions: pause-retains-queue (background/process
        // death = DISARMED, rearm required).
        val events = listOf(
            SessionEvent.Backgrounded,
            SessionEvent.RotationCropChanged,
            SessionEvent.PermissionLost,
            SessionEvent.ProcessRestarted,
        )
        events.forEach { event ->
            assertThat(reduceSession(SessionState.ACTIVE, event))
                .isEqualTo(SessionTransition(SessionState.DISARMED, true))
            assertThat(reduceSession(SessionState.PAUSED, event))
                .isEqualTo(SessionTransition(SessionState.DISARMED, true))
        }
    }

    @Test
    fun sessionCompletionChain() {
        assertThat(reduceSession(SessionState.ACTIVE, SessionEvent.AcquisitionComplete))
            .isEqualTo(SessionTransition(SessionState.ACQUISITION_COMPLETE, true))
        assertThat(reduceSession(SessionState.ACQUISITION_COMPLETE, SessionEvent.AllQualified))
            .isEqualTo(SessionTransition(SessionState.COMPLETE, true))
        assertThat(reduceSession(SessionState.ACQUISITION_COMPLETE, SessionEvent.CompleteWithSkips))
            .isEqualTo(SessionTransition(SessionState.COMPLETE_WITH_SKIPS, true))
    }

    @Test
    fun sessionTerminalIsImmutable() {
        listOf(
            SessionState.EXPIRED,
            SessionState.CANCELLED,
            SessionState.COMPLETE,
            SessionState.COMPLETE_WITH_SKIPS,
        ).forEach { terminal ->
            val arm = SessionEvent.Arm(grantLive = true, permissionGranted = true, foreground = true)
            assertThat(reduceSession(terminal, arm).accepted).isFalse()
        }
    }

    @Test
    fun sessionWireSpelling() {
        assertThat(SessionState.entries.map { it.name }).containsExactly(
            "DISARMED", "ACTIVE", "PAUSED", "EXPIRED", "CANCELLED",
            "ACQUISITION_COMPLETE", "COMPLETE", "COMPLETE_WITH_SKIPS",
        ).inOrder()
    }

    // ---------- Request ----------

    private fun acquireToHold(): RequestState {
        var state = RequestState.QUEUED
        listOf(
            RequestEvent.Accept,
            RequestEvent.EnterNavigating,
            RequestEvent.EnterAligning,
            RequestEvent.GatesPass,
        ).forEach { event ->
            val transition = reduceRequest(state, event)
            assertThat(transition.accepted).isTrue()
            state = transition.state
        }
        assertThat(state).isEqualTo(RequestState.HOLD_STILL)
        return state
    }

    @Test
    fun happyPathCommitsExactlyOneShutter() {
        var state = acquireToHold()
        var shutters = 0
        listOf(RequestEvent.DwellReady, RequestEvent.JpegPersisted, RequestEvent.VerifyPass)
            .forEach { event ->
                val transition = reduceRequest(state, event)
                assertThat(transition.accepted).isTrue()
                if (transition.shutterCommitted) shutters++
                state = transition.state
            }
        assertThat(state).isEqualTo(RequestState.CAPTURED_LOCAL)
        assertThat(shutters).isEqualTo(1)
    }

    @Test
    fun gateFailureReturnsToAligning() {
        val transition = reduceRequest(RequestState.HOLD_STILL, RequestEvent.GateFailed)
        assertThat(transition).isEqualTo(RequestTransition(RequestState.ALIGNING, true))
        assertThat(transition.shutterCommitted).isFalse()
    }

    @Test
    fun retakeBudgetIsThreeAttemptsTwoAutomatic() {
        assertThat(retryAllowed(0, 0, true)).isTrue()
        assertThat(retryAllowed(2, 1, true)).isTrue()
        assertThat(retryAllowed(2, 2, true)).isFalse()
        assertThat(retryAllowed(2, 1, false)).isTrue()
        assertThat(retryAllowed(2, 2, false)).isTrue()
        assertThat(retryAllowed(3, 2, false)).isFalse()
        val denied = reduceRequest(
            RequestState.RETAKE_REQUIRED,
            RequestEvent.Retry(attemptsUsed = 3, autoUsed = 2, automatic = false),
        )
        assertThat(denied.accepted).isFalse()
        val allowed = reduceRequest(
            RequestState.RETAKE_REQUIRED,
            RequestEvent.Retry(attemptsUsed = 1, autoUsed = 1, automatic = true),
        )
        assertThat(allowed).isEqualTo(RequestTransition(RequestState.ALIGNING, true))
    }

    @Test
    fun sessionCancelQueuedAllowsNoShutter() {
        // control-transitions: session-cancel-no-shutter.
        val cancelled = reduceRequest(RequestState.QUEUED, RequestEvent.Cancel)
        assertThat(cancelled).isEqualTo(RequestTransition(RequestState.CANCELLED, true))
        assertThat(reduceRequest(RequestState.CANCELLED, RequestEvent.DwellReady).accepted).isFalse()
    }

    @Test
    fun cancelBeforeLatchCancelsWithoutShutter() {
        // control-transitions: cancel-races-shutter-not-triggered.
        val transition = reduceRequest(RequestState.ALIGNING, RequestEvent.Cancel)
        assertThat(transition).isEqualTo(RequestTransition(RequestState.CANCELLED, true))
        assertThat(transition.shutterCommitted).isFalse()
    }

    @Test
    fun cancelAfterLatchPreservesBytesAsLateAfterCancel() {
        // control-transitions: cancel-races-shutter-triggered.
        val marked = reduceRequest(RequestState.CAPTURING, RequestEvent.Cancel)
        assertThat(marked.state).isEqualTo(RequestState.CAPTURING)
        assertThat(marked.accepted).isTrue()
        assertThat(marked.lateMark).isEqualTo(LateMark.AFTER_CANCEL)
        assertThat(marked.shutterCommitted).isFalse()
        val persisted = reduceRequest(RequestState.CAPTURING, RequestEvent.JpegPersisted, marked.lateMark)
        assertThat(persisted.state).isEqualTo(RequestState.VERIFYING_IMAGE)
        assertThat(persisted.lateMark).isEqualTo(LateMark.AFTER_CANCEL)
        // Late bytes are preserved but can never fulfill the request.
        val verified = reduceRequest(RequestState.VERIFYING_IMAGE, RequestEvent.VerifyPass, marked.lateMark)
        assertThat(verified.state).isEqualTo(RequestState.RETAKE_REQUIRED)
        assertThat(verified.lateMark).isEqualTo(LateMark.AFTER_CANCEL)
    }

    @Test
    fun expireBeforeLatchExpires() {
        // control-transitions: expire-races-shutter.
        assertThat(reduceRequest(RequestState.NAVIGATING, RequestEvent.Expire))
            .isEqualTo(RequestTransition(RequestState.EXPIRED, true))
        val marked = reduceRequest(RequestState.VERIFYING_IMAGE, RequestEvent.Expire)
        assertThat(marked.state).isEqualTo(RequestState.VERIFYING_IMAGE)
        assertThat(marked.lateMark).isEqualTo(LateMark.AFTER_EXPIRE)
    }

    @Test
    fun supersedeInFlightMarksLateAfterSupersede() {
        // control-transitions: supersede-late-bytes.
        val marked = reduceRequest(RequestState.CAPTURING, RequestEvent.Supersede)
        assertThat(marked.state).isEqualTo(RequestState.CAPTURING)
        assertThat(marked.lateMark).isEqualTo(LateMark.AFTER_SUPERSEDE)
        assertThat(reduceRequest(RequestState.ALIGNING, RequestEvent.Supersede))
            .isEqualTo(RequestTransition(RequestState.SUPERSEDED, true))
    }

    @Test
    fun lateRequestReachesTerminalOnSecondCancel() {
        val marked = reduceRequest(RequestState.CAPTURING, RequestEvent.Cancel)
        val persisted = reduceRequest(RequestState.CAPTURING, RequestEvent.JpegPersisted, marked.lateMark)
        val verified = reduceRequest(RequestState.VERIFYING_IMAGE, RequestEvent.VerifyPass, persisted.lateMark)
        assertThat(verified.state).isEqualTo(RequestState.RETAKE_REQUIRED)
        val terminal = reduceRequest(RequestState.RETAKE_REQUIRED, RequestEvent.Cancel, verified.lateMark)
        assertThat(terminal).isEqualTo(RequestTransition(RequestState.CANCELLED, true))
    }

    @Test
    fun markedRetryOnlyManualAfterCancel() {
        val manual = reduceRequest(
            RequestState.RETAKE_REQUIRED,
            RequestEvent.Retry(attemptsUsed = 1, autoUsed = 0, automatic = false),
            LateMark.AFTER_CANCEL,
        )
        assertThat(manual).isEqualTo(RequestTransition(RequestState.ALIGNING, true))
        val auto = reduceRequest(
            RequestState.RETAKE_REQUIRED,
            RequestEvent.Retry(attemptsUsed = 1, autoUsed = 0, automatic = true),
            LateMark.AFTER_CANCEL,
        )
        assertThat(auto.accepted).isFalse()
        val expired = reduceRequest(
            RequestState.RETAKE_REQUIRED,
            RequestEvent.Retry(attemptsUsed = 1, autoUsed = 0, automatic = false),
            LateMark.AFTER_EXPIRE,
        )
        assertThat(expired.accepted).isFalse()
        val superseded = reduceRequest(
            RequestState.RETAKE_REQUIRED,
            RequestEvent.Retry(attemptsUsed = 1, autoUsed = 0, automatic = false),
            LateMark.AFTER_SUPERSEDE,
        )
        assertThat(superseded.accepted).isFalse()
    }

    @Test
    fun revokeInFlightMarksLateAfterRevoke() {
        // control-transitions: revoke-then-qualify (phone side: the
        // attempt can never become CAPTURED_LOCAL on this revision).
        val marked = reduceRequest(RequestState.CAPTURING, RequestEvent.Revoke)
        assertThat(marked.lateMark).isEqualTo(LateMark.AFTER_REVOKE)
        val verified = reduceRequest(RequestState.VERIFYING_IMAGE, RequestEvent.VerifyPass, marked.lateMark)
        assertThat(verified.state).isEqualTo(RequestState.RETAKE_REQUIRED)
    }

    @Test
    fun cantReachParksRequestOnly() {
        // control-transitions: pause-retains-queue ("Can't reach"
        // pauses only the Request, never the Session — the session
        // reducer has no such event by construction).
        assertThat(reduceRequest(RequestState.ALIGNING, RequestEvent.CantReach))
            .isEqualTo(RequestTransition(RequestState.UNREACHABLE, true))
        assertThat(reduceRequest(RequestState.UNREACHABLE, RequestEvent.Requeued))
            .isEqualTo(RequestTransition(RequestState.QUEUED, true))
        // An in-flight shutter finishes; the pause lands after it.
        assertThat(reduceRequest(RequestState.CAPTURING, RequestEvent.CantReach).state)
            .isEqualTo(RequestState.CAPTURING)
    }

    @Test
    fun skippedTargetIsNeverCaptured() {
        assertThat(reduceRequest(RequestState.ALIGNING, RequestEvent.Skip))
            .isEqualTo(RequestTransition(RequestState.CANCELLED, true))
        assertThat(reduceRequest(RequestState.CANCELLED, RequestEvent.DwellReady).accepted).isFalse()
    }

    @Test
    fun requestTerminalIsImmutable() {
        listOf(
            RequestState.CAPTURED_LOCAL,
            RequestState.CANCELLED,
            RequestState.EXPIRED,
            RequestState.SUPERSEDED,
        ).forEach { terminal ->
            assertThat(reduceRequest(terminal, RequestEvent.Cancel).accepted).isFalse()
            assertThat(reduceRequest(terminal, RequestEvent.DwellReady).accepted).isFalse()
        }
    }

    @Test
    fun requestWireSpelling() {
        assertThat(RequestState.entries.map { it.wireName() }).containsExactly(
            "QUEUED", "ACCEPTED", "NAVIGATING", "ALIGNING", "HOLD_STILL",
            "CAPTURING", "VERIFYING_IMAGE", "CAPTURED_LOCAL", "RETAKE_REQUIRED",
            "UNREACHABLE", "CANCELLED", "EXPIRED", "SUPERSEDED",
        ).inOrder()
    }

    // ---------- Delivery ----------

    @Test
    fun deliveryHappyPath() {
        var state = DeliveryState.PENDING_PACKAGE
        listOf(
            DeliveryEvent.StartUpload,
            DeliveryEvent.PackageAccepted,
            DeliveryEvent.SidecarAssociated,
            DeliveryEvent.ServerReceipt(ReceiptKind.QUALIFIED),
        ).forEach { event ->
            val transition = reduceDelivery(state, event)
            assertThat(transition.accepted).isTrue()
            state = transition.state
        }
        assertThat(state).isEqualTo(DeliveryState.ACKNOWLEDGED)
    }

    @Test
    fun secondUploadWhileInFlightIsRejected() {
        assertThat(reduceDelivery(DeliveryState.UPLOADING, DeliveryEvent.StartUpload).accepted)
            .isFalse()
    }

    @Test
    fun retryReusesAttemptAfterResponseLoss() {
        // control-transitions: duplicate-upload-same-bytes (phone
        // side: the retry edge on the same attempt stays open; no new
        // attempt or shutter is created).
        assertThat(reduceDelivery(DeliveryState.UPLOADING, DeliveryEvent.UploadFailedRetryable))
            .isEqualTo(DeliveryTransition(DeliveryState.RETRY_WAIT, true))
        assertThat(reduceDelivery(DeliveryState.RETRY_WAIT, DeliveryEvent.StartUpload))
            .isEqualTo(DeliveryTransition(DeliveryState.UPLOADING, true))
    }

    @Test
    fun packageOnlyStaysPending() {
        // control-transitions: sidecar-before-package.
        val pending = reduceDelivery(
            DeliveryState.PACKAGE_ACCEPTED,
            DeliveryEvent.ServerReceipt(ReceiptKind.PACKAGE_ONLY),
        )
        assertThat(pending).isEqualTo(DeliveryTransition(DeliveryState.SPATIAL_PENDING, true))
        val still = reduceDelivery(
            DeliveryState.SPATIAL_PENDING,
            DeliveryEvent.ServerReceipt(ReceiptKind.PACKAGE_ONLY),
        )
        assertThat(still.state).isEqualTo(DeliveryState.SPATIAL_PENDING)
    }

    @Test
    fun failedSidecarRejects() {
        // control-transitions: package-before-sidecar.
        assertThat(reduceDelivery(DeliveryState.SPATIAL_PENDING, DeliveryEvent.SidecarFailed))
            .isEqualTo(DeliveryTransition(DeliveryState.REJECTED, true))
    }

    @Test
    fun deliveryReceiptMapping() {
        // control-transitions: attempt-delivery-mapping.
        assertThat(deliveryExpectsReceipt(DeliveryState.ACKNOWLEDGED))
            .isEqualTo(ReceiptKind.QUALIFIED)
        assertThat(deliveryExpectsReceipt(DeliveryState.PACKAGE_ACCEPTED))
            .isEqualTo(ReceiptKind.PACKAGE_ONLY)
        assertThat(deliveryExpectsReceipt(DeliveryState.SPATIAL_PENDING))
            .isEqualTo(ReceiptKind.PACKAGE_ONLY)
        assertThat(deliveryExpectsReceipt(DeliveryState.REJECTED))
            .isEqualTo(ReceiptKind.REJECTED)
        // Package acceptance alone never acknowledges.
        assertThat(
            reduceDelivery(DeliveryState.UPLOADING, DeliveryEvent.ServerReceipt(ReceiptKind.QUALIFIED))
                .accepted,
        ).isFalse()
    }

    @Test
    fun lateReceiptMarksAttemptAuditOnly() {
        val marked = reduceDelivery(
            DeliveryState.SPATIAL_PENDING,
            DeliveryEvent.ServerReceipt(ReceiptKind.LATE_AFTER_CANCEL),
        )
        assertThat(marked.state).isEqualTo(DeliveryState.SPATIAL_PENDING)
        assertThat(marked.accepted).isTrue()
        assertThat(marked.lateMark).isEqualTo(LateMark.AFTER_CANCEL)
        // A later qualified receipt cannot acknowledge a late attempt.
        val after = reduceDelivery(
            DeliveryState.SPATIAL_PENDING,
            DeliveryEvent.ServerReceipt(ReceiptKind.QUALIFIED),
            marked.lateMark,
        )
        assertThat(after.state).isEqualTo(DeliveryState.SPATIAL_PENDING)
        assertThat(after.accepted).isTrue()
        // ... and no upload edge advances it either.
        assertThat(
            reduceDelivery(DeliveryState.SPATIAL_PENDING, DeliveryEvent.StartUpload, marked.lateMark)
                .accepted,
        ).isFalse()
    }

    @Test
    fun deliveryTerminalIsImmutable() {
        listOf(DeliveryState.ACKNOWLEDGED, DeliveryState.REJECTED).forEach { terminal ->
            assertThat(reduceDelivery(terminal, DeliveryEvent.StartUpload).accepted).isFalse()
        }
    }

    @Test
    fun deliveryAndReceiptWireSpelling() {
        assertThat(DeliveryState.entries.map { it.name }).containsExactly(
            "PENDING_PACKAGE", "UPLOADING", "RETRY_WAIT", "PACKAGE_ACCEPTED",
            "SPATIAL_PENDING", "ACKNOWLEDGED", "REJECTED",
        ).inOrder()
        assertThat(ReceiptKind.entries.map { it.wire }).containsExactly(
            "package_only", "qualified", "rejected", "late_after_cancel",
            "late_after_expire", "late_after_supersede", "late_after_revoke",
        ).inOrder()
    }

    // ---------- Registration ----------

    @Test
    fun wireRegistrationMapping() {
        assertThat(mapWireRegistration(WireRegistration.VALID))
            .isEqualTo(RegistrationState.ACQUIRED to null)
        assertThat(mapWireRegistration(WireRegistration.AMBIGUOUS))
            .isEqualTo(RegistrationState.DEGRADED to AMBIGUOUS_PLANAR_SOLUTION)
        assertThat(mapWireRegistration(WireRegistration.LOST).first)
            .isEqualTo(RegistrationState.LOST)
        assertThat(mapWireRegistration(WireRegistration.BAD_CALIBRATION).first)
            .isEqualTo(RegistrationState.BAD_CALIBRATION)
        assertThat(mapWireRegistration(WireRegistration.UNREGISTERED).first)
            .isEqualTo(RegistrationState.UNREGISTERED)
    }

    @Test
    fun autoCaptureOnlyWhenAcquired() {
        RegistrationState.entries.forEach { state ->
            assertThat(autoCaptureEligible(state)).isEqualTo(state == RegistrationState.ACQUIRED)
        }
    }

    @Test
    fun lossAndReacquisition() {
        assertThat(reduceRegistration(RegistrationState.ACQUIRED, RegistrationEvent.PoseLost))
            .isEqualTo(RegistrationTransition(RegistrationState.LOST, true))
        assertThat(reduceRegistration(RegistrationState.LOST, RegistrationEvent.Reacquired))
            .isEqualTo(RegistrationTransition(RegistrationState.ACQUIRED, true))
        assertThat(reduceRegistration(RegistrationState.ACQUIRED, RegistrationEvent.PoseAmbiguous))
            .isEqualTo(RegistrationTransition(RegistrationState.DEGRADED, true))
    }

    @Test
    fun badCalibrationBlocksAcquisitionUntilReset() {
        assertThat(
            reduceRegistration(RegistrationState.ACQUIRED, RegistrationEvent.CalibrationInvalid),
        ).isEqualTo(RegistrationTransition(RegistrationState.BAD_CALIBRATION, true))
        assertThat(
            reduceRegistration(RegistrationState.BAD_CALIBRATION, RegistrationEvent.PosesValid)
                .accepted,
        ).isFalse()
        assertThat(reduceRegistration(RegistrationState.BAD_CALIBRATION, RegistrationEvent.Reset))
            .isEqualTo(RegistrationTransition(RegistrationState.UNREGISTERED, true))
    }

    @Test
    fun registrationWireSpelling() {
        assertThat(WireRegistration.entries.map { it.wire }).containsExactly(
            "unregistered", "valid", "degraded", "ambiguous", "lost", "bad_calibration",
        ).inOrder()
    }
}
