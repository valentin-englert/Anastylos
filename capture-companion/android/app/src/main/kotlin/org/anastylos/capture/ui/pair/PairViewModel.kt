package org.anastylos.capture.ui.pair

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.time.Duration
import java.time.Instant
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.anastylos.capture.pairing.ApprovalOutcome
import org.anastylos.capture.pairing.PendingRequest
import org.anastylos.capture.pairing.QrPairClient
import org.anastylos.capture.pairing.QrScanResult
import org.anastylos.capture.pairing.RedeemOutcome
import org.anastylos.capture.pairing.VerifyOutcome
import org.anastylos.capture.pairing.interpretScannedText
import org.anastylos.capture.sync.PairingStore
import org.anastylos.capture.sync.QrPairPayload
import org.anastylos.capture.sync.QrPayloadException
import org.anastylos.capture.sync.StoredPairing
import org.anastylos.capture.sync.parseQrPayload

/**
 * QR pairing flow: scan → review the workstation → confirm → wait
 * for the operator's approval → paired.
 *
 * The scanned payload is untrusted until [parseQrPayload] accepts
 * it; the confirm screen shows the workstation id, endpoint, and
 * code expiry so the operator can cross-check the receiver window.
 * Secrets (pairing code, Bearer [REDACTED] never appear in UI state
 * messages — only in the [QrPairPayload]/store objects the network
 * layer needs, which are never logged.
 */
sealed interface PairScreen {
    data object Idle : PairScreen
    data object Scanning : PairScreen
    data class Invalid(val message: String) : PairScreen
    data class Confirm(val payload: QrPairPayload) : PairScreen
    data class Pairing(
        val payload: QrPairPayload,
        val request: PendingRequest?,
        val verifying: Boolean = false,
    ) : PairScreen
    data class Paired(val workstationId: String, val baseUrl: String) : PairScreen

    /**
     * @param retryAllowed re-running the current phase is meaningful
     *   (transient failure). Otherwise only a fresh QR helps.
     * @param resumeToken when set, retry re-verifies this approved token
     *   instead of re-running redeem/poll (the code is already spent).
     */
    data class Failed(
        val message: String,
        val payload: QrPairPayload,
        val request: PendingRequest?,
        val retryAllowed: Boolean,
        val resumeToken: String? = null,
    ) : PairScreen
}

data class PairUiState(val screen: PairScreen = PairScreen.Idle)

/** "expires in 4:32" / "expires in 38s" / "expired" for the confirm screen. */
fun formatCodeExpiry(expiresAt: Instant, now: Instant): String {
    val remaining = Duration.between(now, expiresAt)
    if (remaining.isNegative || remaining.isZero) return "expired"
    val totalSeconds = remaining.seconds
    return if (totalSeconds >= 60) {
        "expires in ${totalSeconds / 60}:${"%02d".format(totalSeconds % 60)}"
    } else {
        "expires in ${totalSeconds}s"
    }
}

class PairViewModel(
    private val client: QrPairClient,
    private val pairingStore: PairingStore,
    private val deviceLabel: String,
    private val now: () -> Instant = Instant::now,
) : ViewModel() {
    private val _state = MutableStateFlow(PairUiState())
    val state: StateFlow<PairUiState> = _state.asStateFlow()

    private var pairJob: Job? = null

    fun onConnectClicked() {
        when (_state.value.screen) {
            is PairScreen.Idle, is PairScreen.Invalid, is PairScreen.Failed ->
                _state.update { it.copy(screen = PairScreen.Scanning) }
            else -> Unit
        }
    }

    /** One analyzer callback. Ignored unless the scanner is up (single-fire). */
    fun onScanText(rawValue: String?) {
        if (_state.value.screen != PairScreen.Scanning) return
        when (val scan = interpretScannedText(rawValue)) {
            null -> Unit // empty frame; user is still aiming
            is QrScanResult.Unreadable -> _state.update { it.copy(screen = PairScreen.Invalid(scan.reason)) }
            is QrScanResult.Valid -> {
                val payload = try {
                    parseQrPayload(scan.rawText, now())
                } catch (e: QrPayloadException) {
                    // Parser detail names the broken field; cap it so a
                    // hostile 2KB payload cannot flood the screen.
                    val detail = (e.message ?: "malformed payload").take(160)
                    _state.update { it.copy(screen = PairScreen.Invalid("Not a workstation code: $detail")) }
                    return
                }
                _state.update { it.copy(screen = PairScreen.Confirm(payload)) }
            }
        }
    }

    fun onCancelScan() {
        if (_state.value.screen == PairScreen.Scanning) {
            _state.update { it.copy(screen = PairScreen.Idle) }
        }
    }

    fun onRescan() {
        val screen = _state.value.screen
        if (screen is PairScreen.Invalid || screen is PairScreen.Failed) {
            _state.update { it.copy(screen = PairScreen.Scanning) }
        }
    }

    fun onBackFromConfirm() {
        if (_state.value.screen is PairScreen.Confirm) {
            _state.update { it.copy(screen = PairScreen.Scanning) }
        }
    }

    fun onConfirm() {
        val screen = _state.value.screen
        if (screen !is PairScreen.Confirm || pairJob?.isActive == true) return
        _state.update { it.copy(screen = PairScreen.Pairing(screen.payload, null)) }
        pairJob = viewModelScope.launch { runPairing(screen.payload, null) }
    }

    fun onRetry() {
        val screen = _state.value.screen
        if (screen !is PairScreen.Failed || pairJob?.isActive == true) return
        if (!screen.retryAllowed) {
            _state.update { it.copy(screen = PairScreen.Scanning) }
            return
        }
        // Retry resumes the phase that failed: re-verify an approved
        // token, re-poll a live request, or re-redeem only when redeem
        // never succeeded.
        _state.update {
            it.copy(
                screen = PairScreen.Pairing(
                    screen.payload,
                    screen.request,
                    verifying = screen.resumeToken != null,
                ),
            )
        }
        pairJob = viewModelScope.launch { runPairing(screen.payload, screen.request, screen.resumeToken) }
    }

    fun onCancelPairing() {
        if (_state.value.screen is PairScreen.Pairing) {
            pairJob?.cancel()
            pairJob = null
            _state.update { it.copy(screen = PairScreen.Scanning) }
        }
    }

    private suspend fun runPairing(
        payload: QrPairPayload,
        existing: PendingRequest?,
        resumeToken: String? = null,
    ) {
        try {
            if (resumeToken != null) {
                runVerify(payload, existing, resumeToken)
                return
            }
            val request = existing ?: when (val redeem = client.redeem(payload, deviceLabel)) {
                is RedeemOutcome.Pending -> {
                    _state.update { it.copy(screen = PairScreen.Pairing(payload, redeem.request)) }
                    redeem.request
                }
                is RedeemOutcome.CodeInvalid -> return failed(
                    payload, null, false,
                    "That code is already used or expired. Ask the workstation for a fresh QR code.",
                )
                is RedeemOutcome.RateLimited -> return failed(
                    payload, null, true,
                    "Too many attempts — wait a minute, then try again.",
                )
                is RedeemOutcome.PinMismatch -> return failed(
                    payload, null, false,
                    "The workstation's identity does not match the scanned code. " +
                        "Do not retry on this network; ask the operator to check the receiver.",
                )
                is RedeemOutcome.NetworkError -> return failed(
                    payload, null, true,
                    "Could not reach the workstation. Check both devices share the same Wi-Fi, then retry.",
                )
                is RedeemOutcome.ServerError -> return failed(
                    payload, null, true,
                    "The workstation reported an error (${redeem.message.take(160)}). Retry, or ask for a fresh code.",
                )
            }
            when (val approval = client.awaitApproval(payload, request)) {
                is ApprovalOutcome.Approved -> {
                    _state.update { it.copy(screen = PairScreen.Pairing(payload, request, true)) }
                    runVerify(payload, request, approval.token)
                }
                is ApprovalOutcome.NotApproved -> failed(
                    payload, request, false,
                    "Not approved in time. Ask the workstation for a fresh QR code.",
                )
                is ApprovalOutcome.PinMismatch -> failed(
                    payload, request, false,
                    "The workstation's identity changed mid-pairing. " +
                        "Do not retry on this network; ask the operator to check the receiver.",
                )
                is ApprovalOutcome.NetworkError -> failed(
                    payload, request, true,
                    "Lost contact while waiting for approval. The request may still be live — retry to resume polling.",
                )
                is ApprovalOutcome.ServerError -> failed(
                    payload, request, true,
                    "The workstation reported an error (${approval.message.take(160)}). Retry to resume polling.",
                )
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            failed(
                payload, existing, true,
                "Pairing failed (${(e.message ?: "unknown error").take(160)}). Retry, or scan a fresh code.",
                resumeToken = resumeToken,
            )
        }
    }

    /**
     * Confirm the approved token with an authenticated status request.
     * The token is saved only after the server accepts it; transient
     * failures keep the token for a verify-only retry (the code is
     * already spent, so redeem/poll must not run again).
     */
    private suspend fun runVerify(payload: QrPairPayload, request: PendingRequest?, token: String) {
        when (val outcome = client.verify(payload, token)) {
            is VerifyOutcome.Verified -> {
                // The verify echo is the freshest authenticated server
                // identity; the endpoint is the one we connected to.
                pairingStore.save(
                    StoredPairing(
                        baseUrl = payload.baseUrl,
                        workstationId = outcome.workstationId,
                        token = token,
                        pinnedSpkiSha256Hex = spkiHex(payload),
                    ),
                )
                _state.update {
                    it.copy(
                        screen = PairScreen.Paired(
                            workstationId = outcome.workstationId,
                            baseUrl = payload.baseUrl,
                        ),
                    )
                }
            }
            is VerifyOutcome.Unauthorized -> failed(
                payload, request, false,
                "The workstation issued a token but rejected it. Ask the operator for a fresh QR code.",
            )
            is VerifyOutcome.PinMismatch -> failed(
                payload, request, false,
                "The workstation's identity does not match the scanned code. " +
                    "Do not retry on this network; ask the operator to check the receiver.",
            )
            is VerifyOutcome.NetworkError -> failed(
                payload, request, true,
                "Approved, but the confirmation didn't go through. Retry to confirm — the approval is kept.",
                resumeToken = token,
            )
            is VerifyOutcome.ServerError -> failed(
                payload, request, true,
                "Approved, but the workstation reported an error (${outcome.message.take(160)}). " +
                    "Retry to confirm — the approval is kept.",
                resumeToken = token,
            )
        }
    }

    private fun failed(
        payload: QrPairPayload,
        request: PendingRequest?,
        retryAllowed: Boolean,
        message: String,
        resumeToken: String? = null,
    ) {
        _state.update {
            it.copy(screen = PairScreen.Failed(message, payload, request, retryAllowed, resumeToken))
        }
    }

    private fun spkiHex(payload: QrPairPayload): String =
        payload.spkiSha256.joinToString("") { "%02x".format(it) }
}
