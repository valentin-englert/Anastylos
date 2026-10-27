package org.anastylos.capture.ui.session

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.security.cert.X509Certificate
import java.time.Instant
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.anastylos.capture.camera.CapturedStill
import org.anastylos.capture.camera.FrameSampler
import org.anastylos.capture.camera.SamplerEvent
import org.anastylos.capture.camera.SamplingConfig
import org.anastylos.capture.camera.launchSampling
import org.anastylos.capture.data.EvidenceRepository
import org.anastylos.capture.data.SamplingPrefs
import org.anastylos.capture.data.files.EvidenceFileStore
import org.anastylos.capture.domain.CaptureSession
import org.anastylos.capture.domain.Transport
import org.anastylos.capture.sync.FrameOutcome
import org.anastylos.capture.sync.FrameUploader
import org.anastylos.capture.sync.HttpTransport
import org.anastylos.capture.sync.PairOutcome
import org.anastylos.capture.sync.PairingStore
import org.anastylos.capture.sync.PinnedTls
import org.anastylos.capture.sync.ReceiverClient
import org.anastylos.capture.sync.StoredPairing
import org.anastylos.capture.sync.certSha256Hex
import org.anastylos.capture.sync.parseHexSha256
import org.anastylos.capture.sync.peekServerCertificates
import org.anastylos.capture.sync.pinnedSocketFactory
import org.anastylos.capture.sync.pinnedSpkiSocketFactory

data class SessionUiState(
    val sessions: List<CaptureSession> = emptyList(),
    val activeSessionId: String? = null,
    val intervalSeconds: Int = 5,
    val sampling: Boolean = false,
    val framesStored: Int = 0,
    val framesMissed: Int = 0,
    val lastFrameAt: Instant? = null,
    val pairing: StoredPairing? = null,
    val pairingBusy: Boolean = false,
    val pairingError: String? = null,
    val pendingCount: Int = 0,
    val syncedCount: Int = 0,
    val uploading: Boolean = false,
    val lastUpload: String? = null,
    val error: String? = null,
)

/**
 * Slice A session screen logic: sessions, sampling loop, manual pairing,
 * and pending upload. No business logic lives in the composables; every
 * collaborator is constructor-injected so this class unit-tests on the
 * JVM with fakes (no camera, no network, no Keystore).
 */
class SessionViewModel(
    private val repo: EvidenceRepository,
    private val files: EvidenceFileStore,
    private val sampler: FrameSampler,
    private val pairingStore: PairingStore,
    private val prefs: SamplingPrefs,
    private val appVersion: String,
    private val transportFactory: (baseUrl: String, tls: PinnedTls?) -> HttpTransport,
    private val peekCertificates: suspend (host: String, port: Int) -> Array<X509Certificate> =
        { host, port -> withContext(Dispatchers.IO) { peekServerCertificates(host, port) } },
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : ViewModel() {
    private val _state = MutableStateFlow(SessionUiState(pairing = pairingStore.load()))
    val state: StateFlow<SessionUiState> = _state.asStateFlow()

    private var samplerJob: Job? = null

    init {
        viewModelScope.launch {
            repo.observeSessions().collect { sessions ->
                _state.update { it.copy(sessions = sessions) }
            }
        }
        viewModelScope.launch {
            prefs.intervalSeconds.collect { seconds ->
                _state.update { it.copy(intervalSeconds = seconds) }
            }
        }
    }

    fun createSession(name: String) {
        viewModelScope.launch {
            try {
                val session = repo.createSession(name)
                _state.update { it.copy(activeSessionId = session.sessionId, error = null) }
                refreshCounts(session.sessionId)
            } catch (e: Exception) {
                _state.update { it.copy(error = e.message ?: "could not create the session") }
            }
        }
    }

    fun selectSession(id: String) {
        _state.update { it.copy(activeSessionId = id, lastUpload = null) }
        refreshCounts(id)
    }

    fun setInterval(seconds: Int) {
        viewModelScope.launch {
            try {
                prefs.setIntervalSeconds(seconds)
            } catch (e: Exception) {
                _state.update { it.copy(error = e.message ?: "invalid interval") }
            }
        }
    }

    fun startSampling(captureStill: suspend () -> CapturedStill) {
        val sessionId = _state.value.activeSessionId
        if (sessionId == null || samplerJob?.isActive == true) return
        val config = try {
            SamplingConfig(_state.value.intervalSeconds)
        } catch (e: Exception) {
            _state.update { it.copy(error = e.message) }
            return
        }
        _state.update { it.copy(sampling = true, error = null) }
        samplerJob = sampler.launchSampling(
            viewModelScope,
            sessionId,
            config,
            captureStill,
            onEvent = { event ->
                when (event) {
                    is SamplerEvent.FrameStored -> _state.update {
                        it.copy(
                            framesStored = it.framesStored + 1,
                            lastFrameAt = event.capturedAt,
                        )
                    }
                    is SamplerEvent.FrameMissed -> _state.update {
                        it.copy(framesMissed = it.framesMissed + 1)
                    }
                }
                refreshCounts(sessionId)
            },
        )
        samplerJob?.invokeOnCompletion {
            _state.update { it.copy(sampling = false) }
        }
    }

    fun stopSampling() {
        samplerJob?.cancel()
        samplerJob = null
    }

    fun pair(host: String, portText: String, code: String, fingerprintHex: String) {
        if (_state.value.pairingBusy) return
        _state.update { it.copy(pairingBusy = true, pairingError = null) }
        viewModelScope.launch {
            try {
                val cleanHost = host.trim()
                require(cleanHost.isNotEmpty()) { "enter the workstation host" }
                val port = portText.trim().toIntOrNull()
                require(port != null && port in 1..65_535) { "port must be 1..65535" }
                val expectedPin = try {
                    parseHexSha256(fingerprintHex)
                } catch (_: IllegalArgumentException) {
                    throw IllegalArgumentException("fingerprint must be 64 hex characters")
                }
                val chain = try {
                    peekCertificates(cleanHost, port)
                } catch (e: Exception) {
                    throw IllegalStateException("workstation unreachable: ${e.message}")
                }
                require(chain.isNotEmpty()) { "workstation presented no certificate" }
                val actualPin = parseHexSha256(certSha256Hex(chain[0]))
                require(java.security.MessageDigest.isEqual(actualPin, expectedPin)) {
                    "fingerprint does not match the receiver window"
                }
                val baseUrl = "https://$cleanHost:$port"
                val tls = pinnedSocketFactory(expectedPin)
                val client = ReceiverClient(transportFactory(baseUrl, tls), ioDispatcher)
                when (val outcome = client.pair(code.trim())) {
                    is PairOutcome.Paired -> {
                        val stored = StoredPairing(
                            baseUrl = baseUrl,
                            workstationId = outcome.pairing.workstationId,
                            token = outcome.pairing.token,
                            pinnedCertSha256Hex = certSha256Hex(chain[0]),
                        )
                        pairingStore.save(stored)
                        _state.update { it.copy(pairing = stored, pairingBusy = false) }
                    }
                    is PairOutcome.Rejected ->
                        throw IllegalStateException("pairing rejected: ${outcome.message}")
                    is PairOutcome.RateLimited ->
                        throw IllegalStateException("too many attempts; wait and retry")
                    is PairOutcome.Failed ->
                        throw IllegalStateException(outcome.message)
                }
            } catch (e: Exception) {
                // Secrets never reach the UI state: codes and tokens stay out
                // of every message by construction (no echo of inputs).
                _state.update {
                    it.copy(pairingBusy = false, pairingError = e.message ?: "pairing failed")
                }
            }
        }
    }

    fun unpair() {
        pairingStore.clear()
        _state.update { it.copy(pairing = null, pairingError = null) }
    }

    /**
     * Re-read the pairing store (a QR flow on another screen may have
     * saved a pairing while this screen was away).
     */
    fun refreshPairing() {
        _state.update { it.copy(pairing = pairingStore.load()) }
    }

    fun uploadPending() {
        val pairing = _state.value.pairing
        val sessionId = _state.value.activeSessionId
        if (pairing == null || sessionId == null || _state.value.uploading) return
        _state.update { it.copy(uploading = true, lastUpload = null, error = null) }
        viewModelScope.launch {
            try {
                val observations = repo.getAllObservations().filter { it.sessionId == sessionId }
                val imagesById = observations.flatMap { it.images }.associateBy { it.imageId }
                val tls = pairingTls(pairing)
                val client = ReceiverClient(transportFactory(pairing.baseUrl, tls), ioDispatcher)
                val uploader = FrameUploader(
                    repo = repo,
                    resolveOriginal = { imageId ->
                        val image = imagesById[imageId] ?: return@FrameUploader null
                        val ext = image.filename.substringAfterLast('.', "")
                        val file = files.originalFile(image.observationId, imageId, ext)
                        if (file.isFile) file.readBytes() else null
                    },
                    client = client,
                    token = pairing.token,
                    appVersion = appVersion,
                )
                val run = uploader.uploadSession(sessionId)
                val synced = run.outcomes.values.count { it is FrameOutcome.Synced }
                val rejected = run.outcomes.values.count { it is FrameOutcome.Rejected }
                val failed = run.outcomes.values.count {
                    it is FrameOutcome.Failed || it is FrameOutcome.Blocked
                }
                _state.update {
                    it.copy(
                        uploading = false,
                        lastUpload = "$synced synced, $rejected rejected, $failed failed",
                    )
                }
                refreshCounts(sessionId)
            } catch (e: Exception) {
                _state.update {
                    it.copy(uploading = false, error = e.message ?: "upload failed")
                }
            }
        }
    }

    fun dismissError() {
        _state.update { it.copy(error = null, pairingError = null) }
    }

    private fun pairingTls(pairing: StoredPairing): PinnedTls {
        if (pairing.pinnedCertSha256Hex.isNotBlank()) {
            return pinnedSocketFactory(parseHexSha256(pairing.pinnedCertSha256Hex))
        }
        require(pairing.pinnedSpkiSha256Hex.isNotBlank()) { "pairing holds no pin; re-pair" }
        return pinnedSpkiSocketFactory(parseHexSha256(pairing.pinnedSpkiSha256Hex))
    }

    private fun refreshCounts(sessionId: String) {
        viewModelScope.launch {
            val observations = try {
                repo.getAllObservations().filter { it.sessionId == sessionId }
            } catch (_: Exception) {
                return@launch
            }
            val synced = observations.count { obs -> obs.exportHistory.any { it.via == Transport.SYNC } }
            _state.update {
                if (it.activeSessionId != sessionId) it
                else it.copy(pendingCount = observations.size - synced, syncedCount = synced)
            }
        }
    }
}
