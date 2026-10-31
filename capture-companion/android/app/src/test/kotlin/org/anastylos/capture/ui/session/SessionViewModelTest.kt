package org.anastylos.capture.ui.session

import com.google.common.truth.Truth.assertThat
import java.time.Instant
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.anastylos.capture.camera.CapturedStill
import org.anastylos.capture.camera.FrameSampler
import org.anastylos.capture.camera.UsableCamera
import org.anastylos.capture.camera.CameraSettings
import org.anastylos.capture.camera.DEFAULT_PROFILE
import org.anastylos.capture.camera.ExposureMode
import org.anastylos.capture.data.CameraPrefs
import org.anastylos.capture.data.CameraSettingsStore
import org.anastylos.capture.data.EvidenceRepository
import org.anastylos.capture.data.SamplingPrefs
import org.anastylos.capture.data.files.EvidenceFileStore
import org.anastylos.capture.domain.CaptureSession
import org.anastylos.capture.domain.CaptureSource
import org.anastylos.capture.domain.ComponentObservation
import org.anastylos.capture.domain.ConfirmedField
import org.anastylos.capture.domain.EpistemicState
import org.anastylos.capture.domain.EvidenceImage
import org.anastylos.capture.domain.ExportRecord
import org.anastylos.capture.domain.ImageRole
import org.anastylos.capture.domain.ReviewState
import org.anastylos.capture.domain.Transport
import org.anastylos.capture.domain.newId
import org.anastylos.capture.domain.nowUtc
import org.anastylos.capture.domain.sha256Hex
import org.anastylos.capture.sync.EphemeralPairingStore
import org.anastylos.capture.sync.HttpRequest
import org.anastylos.capture.sync.HttpResponse
import org.anastylos.capture.sync.HttpTransport
import org.anastylos.capture.sync.PinnedTls
import org.anastylos.capture.sync.StoredPairing
import org.anastylos.capture.sync.loadPemCertificates
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class SessionViewModelTest {
    @get:Rule
    val temp = TemporaryFolder()

    private class FakeRepository : EvidenceRepository {
        val sessions = mutableMapOf<String, CaptureSession>()
        val observations = mutableMapOf<String, ComponentObservation>()
        val images = mutableListOf<EvidenceImage>()
        private val sessionsFlow = MutableStateFlow<List<CaptureSession>>(emptyList())

        override fun observeSessions(): Flow<List<CaptureSession>> = sessionsFlow
        override fun observeSession(id: String): Flow<CaptureSession?> = flowOf(sessions[id])
        override suspend fun getSession(id: String): CaptureSession? = sessions[id]
        override suspend fun createSession(name: String): CaptureSession {
            require(name.isNotBlank()) { "name: must not be blank" }
            val now = nowUtc()
            val session = CaptureSession(newId(), name.trim(), now, now, null, null)
            sessions[session.sessionId] = session
            sessionsFlow.value = sessions.values.toList()
            return session
        }

        override suspend fun renameSession(id: String, name: String) = TODO()
        override suspend fun deleteSession(id: String) = TODO()
        override fun observeObservations(sessionId: String): Flow<List<ComponentObservation>> =
            flowOf(observations.values.filter { it.sessionId == sessionId })

        override fun observeObservation(id: String): Flow<ComponentObservation?> =
            flowOf(observations[id])

        override suspend fun getObservation(id: String): ComponentObservation? = observations[id]
        override suspend fun getAllObservations(): List<ComponentObservation> =
            observations.values.toList()

        override suspend fun createObservation(
            sessionId: String,
            source: CaptureSource,
        ): ComponentObservation {
            require(sessions.containsKey(sessionId)) { "unknown session: $sessionId" }
            val obs = ComponentObservation(
                observationId = newId(),
                sessionId = sessionId,
                componentReference = null,
                trayReference = null,
                capturedAt = nowUtc(),
                captureSource = source,
                reviewState = ReviewState.UNREVIEWED,
                operatorNotes = null,
                markings = emptyList(),
                category = ConfirmedField(null, EpistemicState.UNKNOWN),
                measurements = emptyList(),
                quantity = ConfirmedField(null, EpistemicState.UNKNOWN),
                candidates = emptyList(),
                appVersion = "0.1.0",
                userAgent = null,
                exportHistory = emptyList(),
                images = emptyList(),
            )
            observations[obs.observationId] = obs
            return obs
        }

        override suspend fun saveObservation(obs: ComponentObservation) {
            observations[obs.observationId] = obs
        }

        override suspend fun deleteObservation(id: String) {
            observations.remove(id)
        }

        override suspend fun recordImage(image: EvidenceImage) {
            require(observations.containsKey(image.observationId)) {
                "unknown observation: ${image.observationId}"
            }
            images += image
            val current = observations.getValue(image.observationId)
            observations[image.observationId] = current.copy(images = current.images + image)
        }

        override suspend fun markSynced(
            observationIds: List<String>,
            packageId: String,
            exportedAt: Instant,
        ) {
            for (id in observationIds) {
                val current = observations[id] ?: continue
                observations[id] = current.copy(
                    exportHistory = current.exportHistory +
                        ExportRecord(packageId, exportedAt, Transport.SYNC),
                )
            }
        }
    }

    private class FakePrefs(initial: Int = 5) : SamplingPrefs {
        private val flow = MutableStateFlow(initial)
        override val intervalSeconds: Flow<Int> = flow
        override suspend fun setIntervalSeconds(seconds: Int) {
            require(seconds in 1..300) { "sampling interval must be 1..300 seconds" }
            flow.value = seconds
        }
    }

    private class FakeCameraPrefs : CameraPrefs {
        private val flow = MutableStateFlow<String?>(null)
        override val selectedCameraId: Flow<String?> = flow
        val saved = mutableListOf<String>()
        override suspend fun setSelectedCameraId(id: String) {
            saved += id
            flow.value = id
        }
    }

    private class FakeSettingsStore : CameraSettingsStore {
        private val flows = mutableMapOf<String, MutableStateFlow<CameraSettings>>()
        private val profileFlow = MutableStateFlow(DEFAULT_PROFILE)
        val updates = mutableListOf<Triple<String, String, CameraSettings>>()
        override fun settings(cameraId: String, profile: String): Flow<CameraSettings> =
            flows.getOrPut("$cameraId/$profile") { MutableStateFlow(CameraSettings()) }
        override suspend fun update(cameraId: String, profile: String, value: CameraSettings) {
            updates += Triple(cameraId, profile, value)
            flows.getOrPut("$cameraId/$profile") { MutableStateFlow(value) }.value = value
        }
        override val activeProfile: Flow<String> = profileFlow
        override suspend fun setActiveProfile(profile: String) { profileFlow.value = profile }
    }

    private class FakeTransport(
        private val handler: (HttpRequest) -> HttpResponse,
    ) : HttpTransport {
        val requests = mutableListOf<HttpRequest>()
        override fun exchange(request: HttpRequest): HttpResponse {
            requests += request
            return handler(request)
        }
    }

    private val workstationCert = lazy {
        val bytes = requireNotNull(javaClass.getResource("/tls/workstation.pem")).readBytes()
        loadPemCertificates(bytes).single()
    }

    private fun TestScope.viewModel(
        repo: FakeRepository,
        handler: (HttpRequest) -> HttpResponse = { HttpResponse(500, ByteArray(0)) },
        peek: suspend (String, Int) -> Array<java.security.cert.X509Certificate> =
            { _, _ -> arrayOf(workstationCert.value) },
        tlsSeen: MutableList<Pair<String, PinnedTls?>>? = null,
        cameraPrefs: CameraPrefs? = null,
        settingsStore: CameraSettingsStore? = null,
    ): Triple<SessionViewModel, FakeTransport, EphemeralPairingStore> {
        // One scheduler for everything: Main, client IO, file IO, sampler.
        // No real-dispatcher hop remains, so virtual time is exact.
        val testDispatcher = StandardTestDispatcher(testScheduler)
        Dispatchers.setMain(testDispatcher)
        val transport = FakeTransport(handler)
        val pairingStore = EphemeralPairingStore()
        val files = EvidenceFileStore(temp.root, testDispatcher)
        val vm = SessionViewModel(
            repo = repo,
            files = files,
            sampler = FrameSampler(repo, files, ioDispatcher = testDispatcher),
            pairingStore = pairingStore,
            prefs = FakePrefs(),
            cameraPrefs = cameraPrefs,
            settingsStore = settingsStore,
            appVersion = "0.1.0",
            transportFactory = { baseUrl, tls -> tlsSeen?.add(baseUrl to tls); transport },
            peekCertificates = peek,
            ioDispatcher = testDispatcher,
        )
        return Triple(vm, transport, pairingStore)
    }

    @After
    fun tearDownMain() {
        Dispatchers.resetMain()
    }

    @Test
    fun `creating a session activates it`() = runTest {
        val (vm, _, _) = viewModel(FakeRepository())
        vm.createSession("  SNES teardown  ")
        advanceTimeBy(100)
        assertThat(vm.state.value.activeSessionId).isNotNull()
        assertThat(vm.state.value.sessions.single().name).isEqualTo("SNES teardown")

        vm.createSession("   ")
        advanceTimeBy(100)
        assertThat(vm.state.value.error).contains("must not be blank")
    }

    @Test
    fun `pairing verifies the fingerprint before sending the code`() = runTest {
        val (vm, transport, store) = viewModel(FakeRepository(), handler = {
            HttpResponse(
                200,
                """{"workstation_id": "ws-1", "token": "tok-secret"}""".toByteArray(),
            )
        })
        vm.pair(
            "192.168.1.2",
            "8443",
            "alpha bravo charlie delta echo foxtrot",
            "73556d40a654297a953619b3a08259ce2dfe749d61e6b88e2699614bc715817d",
        )
        advanceTimeBy(100)

        assertThat(transport.requests).hasSize(1)
        assertThat(transport.requests.single().body!!.toString(Charsets.UTF_8))
            .contains("alpha bravo")
        assertThat(vm.state.value.pairing?.workstationId).isEqualTo("ws-1")
        assertThat(store.load()?.token).isEqualTo("tok-secret")
        assertThat(vm.state.value.pairingBusy).isFalse()
    }

    @Test
    fun `pairing sends nothing on fingerprint mismatch`() = runTest {
        val (vm, transport, store) = viewModel(FakeRepository(), handler = {
            HttpResponse(200, """{"workstation_id": "ws-x", "token": "tok-x"}""".toByteArray())
        })
        vm.pair("192.168.1.2", "8443", "some code", "00".repeat(32))
        advanceTimeBy(100)

        assertThat(transport.requests).isEmpty()
        assertThat(store.load()).isNull()
        assertThat(vm.state.value.pairing).isNull()
        assertThat(vm.state.value.pairingError).contains("does not match")
        assertThat(vm.state.value.pairingError).doesNotContain("some code")
    }

    @Test
    fun `pairing reports unreachable hosts and rejections`() = runTest {
        val (unreachable, transport1, _) = viewModel(
            FakeRepository(),
            peek = { _, _ -> throw java.io.IOException("connection refused") },
        )
        unreachable.pair("192.168.1.9", "8443", "code", "00".repeat(32))
        advanceTimeBy(100)
        assertThat(unreachable.state.value.pairingError).contains("unreachable")
        assertThat(transport1.requests).isEmpty()

        val (rejected, _, store2) = viewModel(FakeRepository(), handler = {
            HttpResponse(403, """{"error": {"code": "x", "message": "bad code"}}""".toByteArray())
        })
        rejected.pair(
            "192.168.1.2",
            "8443",
            "wrong code",
            "73556d40a654297a953619b3a08259ce2dfe749d61e6b88e2699614bc715817d",
        )
        advanceTimeBy(100)
        assertThat(rejected.state.value.pairingError).contains("bad code")
        assertThat(rejected.state.value.pairingError).doesNotContain("wrong code")
        assertThat(store2.load()).isNull()
    }

    @Test
    fun `upload sends waiting frames and reports the run`() = runTest {
        val repo = FakeRepository()
        val (vm, transport, _) = viewModel(repo, handler = { request ->
            if (request.path == "/v1/pair") {
                HttpResponse(
                    200,
                    """{"workstation_id": "ws-1", "token": "tok"}""".toByteArray(),
                )
            } else {
                val id = request.headers["X-Package-Id"]!!
                HttpResponse(
                    201,
                    """{"duplicate": false, "report": {"package_id": "$id", "status": "valid"}}"""
                        .toByteArray(),
                )
            }
        })
        vm.createSession("demo")
        advanceTimeBy(100)
        val sessionId = vm.state.value.activeSessionId!!
        vm.pair(
            "192.168.1.2",
            "8443",
            "alpha bravo",
            "73556d40a654297a953619b3a08259ce2dfe749d61e6b88e2699614bc715817d",
        )
        advanceTimeBy(100)
        assertThat(vm.state.value.pairing?.workstationId).isEqualTo("ws-1")
        // Same temp root as the VM's store, so upload resolves the bytes.
        val sampler = FrameSampler(repo, EvidenceFileStore(temp.root))
        sampler.adoptFrame(
            sessionId,
            CapturedStill(
                byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0x01),
                Instant.parse("2026-10-27T09:00:05.000Z"),
            ),
            1,
        )
        vm.uploadPending()
        advanceTimeBy(5_000)

        assertThat(vm.state.value.lastUpload).isEqualTo("1 synced, 0 rejected, 0 failed")
        assertThat(repo.getAllObservations().single().exportHistory).hasSize(1)
        assertThat(transport.requests.map { it.path }).containsExactly("/v1/pair", "/v1/packages")
        assertThat(vm.state.value.pendingCount).isEqualTo(0)
        assertThat(vm.state.value.syncedCount).isEqualTo(1)
    }

    @Test
    fun `refreshPairing picks up externally saved pairings`() = runTest {
        val (vm, _, store) = viewModel(FakeRepository())
        assertThat(vm.state.value.pairing).isNull()
        store.save(
            StoredPairing(
                baseUrl = "https://192.168.1.2:8443",
                workstationId = "ws-9",
                token = "tok",
                pinnedSpkiSha256Hex = "5b990e9c6186bf1163ff0c7d2cef2f156e92c57a3b4f3cdd43b98800977b9206",
            ),
        )
        vm.refreshPairing()
        assertThat(vm.state.value.pairing?.workstationId).isEqualTo("ws-9")
    }

    @Test
    fun `upload over an SPKI pairing pins the key`() = runTest {
        val repo = FakeRepository()
        val tlsSeen = mutableListOf<Pair<String, PinnedTls?>>()
        val (vm, _, store) = viewModel(repo, handler = { request ->
            val id = request.headers["X-Package-Id"]!!
            HttpResponse(
                201,
                """{"duplicate": false, "report": {"package_id": "$id", "status": "valid"}}"""
                    .toByteArray(),
            )
        }, tlsSeen = tlsSeen)
        store.save(
            StoredPairing(
                baseUrl = "https://192.168.1.2:8443",
                workstationId = "ws-9",
                token = "tok",
                pinnedSpkiSha256Hex = "5b990e9c6186bf1163ff0c7d2cef2f156e92c57a3b4f3cdd43b98800977b9206",
            ),
        )
        vm.refreshPairing()
        vm.createSession("demo")
        advanceTimeBy(100)
        val sessionId = vm.state.value.activeSessionId!!
        // Same temp root as the VM's store, so upload resolves the bytes.
        val sampler = FrameSampler(repo, EvidenceFileStore(temp.root))
        sampler.adoptFrame(
            sessionId,
            CapturedStill(
                byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0x01),
                Instant.parse("2026-10-27T09:00:05.000Z"),
            ),
            1,
        )
        vm.uploadPending()
        advanceTimeBy(5_000)

        assertThat(vm.state.value.lastUpload).isEqualTo("1 synced, 0 rejected, 0 failed")
        assertThat(tlsSeen).hasSize(1)
        assertThat(tlsSeen.single().first).isEqualTo("https://192.168.1.2:8443")
        assertThat(tlsSeen.single().second).isNotNull()
    }

    @Test
    fun `upload without pairing sends nothing`() = runTest {
        val (vm, transport, _) = viewModel(FakeRepository())
        vm.uploadPending()
        advanceTimeBy(100)
        assertThat(transport.requests).isEmpty()
        assertThat(vm.state.value.lastUpload).isNull()
    }

    @Test
    fun `sampling stores frames on the virtual clock`() = runTest {
        val repo = FakeRepository()
        val (vm, _, _) = viewModel(repo)
        vm.createSession("demo")
        advanceTimeBy(100)
        var ticks = 0
        vm.startSampling {
            ticks++
            CapturedStill(
                byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), ticks.toByte()),
                Instant.parse("2026-10-27T09:00:0${ticks}.000Z"),
            )
        }
        advanceTimeBy(12_000)
        vm.stopSampling()
        advanceTimeBy(100)

        assertThat(vm.state.value.sampling).isFalse()
        assertThat(vm.state.value.framesStored).isAtLeast(2)
        assertThat(repo.images).hasSize(vm.state.value.framesStored)
    }

    @Test
    fun `sampling without a session is a no-op`() = runTest {
        val (vm, _, _) = viewModel(FakeRepository())
        vm.startSampling { throw AssertionError("must not capture without a session") }
        advanceTimeBy(1_000)
        assertThat(vm.state.value.sampling).isFalse()
        assertThat(vm.state.value.framesStored).isEqualTo(0)
    }

    @Test
    fun `interval changes persist and validate`() = runTest {
        val (vm, _, _) = viewModel(FakeRepository())
        advanceTimeBy(100)
        assertThat(vm.state.value.intervalSeconds).isEqualTo(5)
        vm.setInterval(30)
        advanceTimeBy(100)
        assertThat(vm.state.value.intervalSeconds).isEqualTo(30)
        vm.setInterval(0)
        advanceTimeBy(100)
        assertThat(vm.state.value.error).contains("1..300")
    }

    @Test
    fun selectCameraPersistsSelection() = runTest {
        val prefs = FakeCameraPrefs()
        val (vm, _, _) = viewModel(FakeRepository(), cameraPrefs = prefs)
        vm.selectCamera("2")
        advanceTimeBy(100)
        assertThat(prefs.saved).containsExactly("2")
        assertThat(vm.state.value.selectedCameraId).isEqualTo("2")
    }

    @Test
    fun cameraInventorySurfacesInState() = runTest {
        val (vm, _, _) = viewModel(FakeRepository())
        vm.onCamerasListed(
            listOf(UsableCamera("0", "Back 0", 1, 5.2f, null, null, true, "LIMITED")),
            "0", null,
        )
        assertThat(vm.state.value.availableCameras).hasSize(1)
        assertThat(vm.state.value.effectiveCameraId).isEqualTo("0")
        assertThat(vm.state.value.cameraNote).isNull()
    }

    @Test
    fun updateSettingsPersistsSanitized() = runTest {
        val store = FakeSettingsStore()
        val (vm, _, _) = viewModel(FakeRepository(), settingsStore = store)
        val cam = UsableCamera(
            "0", "Back 0", 1, null, null, null, false, null,
            org.anastylos.capture.camera.CameraCapabilities(),
        )
        vm.onCamerasListed(listOf(cam), "0", null)
        vm.updateSettings { it.copy(exposureMode = ExposureMode.MANUAL, torch = true) }
        advanceTimeBy(100)
        assertThat(store.updates).hasSize(1)
        assertThat(store.updates[0].third.exposureMode).isEqualTo(ExposureMode.AUTO)
        assertThat(store.updates[0].third.torch).isFalse()
    }

    @Test
    fun failedApplySurfacesNote() = runTest {
        val (vm, _, _) = viewModel(FakeRepository())
        vm.onSettingsApplied(false)
        assertThat(vm.state.value.settingsNote).isEqualTo("settings not applied")
        vm.onSettingsApplied(true)
        assertThat(vm.state.value.settingsNote).isNull()
    }
}
