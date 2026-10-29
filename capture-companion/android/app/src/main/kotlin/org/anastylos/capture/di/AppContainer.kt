package org.anastylos.capture.di

import android.app.Application
import android.content.Context
import android.os.Build
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import java.io.File
import org.anastylos.capture.BuildConfig
import org.anastylos.capture.camera.FrameSampler
import org.anastylos.capture.data.DataStoreSamplingPrefs
import org.anastylos.capture.data.EvidenceFileStorePort
import org.anastylos.capture.data.EvidenceRepository
import org.anastylos.capture.data.RoomEvidenceRepository
import org.anastylos.capture.camera.DefaultCameraProviderSource
import org.anastylos.capture.camera.GuidedCameraSource
import org.anastylos.capture.data.DataStoreGuidedPrefs
import org.anastylos.capture.data.GuidedPrefs
import org.anastylos.capture.data.SamplingPrefs
import org.anastylos.capture.guidance.OpenCvPoseEstimator
import org.anastylos.capture.guidance.PoseEstimator
import org.anastylos.capture.ui.guided.GuidanceViewModel
import org.anastylos.capture.data.files.EvidenceFileStore
import org.anastylos.capture.data.local.CaptureDatabase
import org.anastylos.capture.domain.nowUtc
import org.anastylos.capture.pairing.HttpQrPairClient
import org.anastylos.capture.pairing.QrPairClient
import org.anastylos.capture.pairing.buildDeviceLabel
import org.anastylos.capture.sync.EncryptedPairingStore
import org.anastylos.capture.sync.HttpsTransport
import org.anastylos.capture.sync.PairingStore
import org.anastylos.capture.sync.pinnedSpkiSocketFactory
import org.anastylos.capture.ui.pair.PairViewModel
import org.anastylos.capture.ui.session.SessionViewModel

class CaptureApp : Application() {
    val container by lazy { AppContainer(this) }
}

/** Manual dependency wiring. Small enough to read; no framework needed yet. */
class AppContainer(context: Context) {
    private val appContext = context.applicationContext

    val database: CaptureDatabase by lazy { CaptureDatabase.open(appContext) }

    val fileStore: EvidenceFileStore by lazy {
        EvidenceFileStore(File(appContext.filesDir, "evidence"))
    }

    private val filePort = object : EvidenceFileStorePort {
        override fun deleteObservationFiles(observationId: String) {
            fileStore.deleteObservationFiles(observationId)
        }
    }

    val repository: EvidenceRepository by lazy {
        RoomEvidenceRepository(database, filePort, ::nowUtc, BuildConfig.VERSION_NAME)
    }

    val sampler: FrameSampler by lazy {
        FrameSampler(repository, fileStore, ::nowUtc)
    }

    val pairingStore: PairingStore by lazy {
        EncryptedPairingStore(appContext)
    }

    val samplingPrefs: SamplingPrefs by lazy {
        DataStoreSamplingPrefs(appContext)
    }

    val qrPairClient: QrPairClient by lazy {
        HttpQrPairClient(
            transportFactory = { payload ->
                HttpsTransport(payload.baseUrl, pinnedSpkiSocketFactory(payload.spkiSha256))
            },
        )
    }

    private val guidedPrefs: GuidedPrefs by lazy { DataStoreGuidedPrefs(appContext) }
    private val poseEstimator: PoseEstimator by lazy { OpenCvPoseEstimator() }

    @Suppress("UNCHECKED_CAST")
    fun guidedViewModelFactory(): ViewModelProvider.Factory =
        object : ViewModelProvider.Factory {
            override fun <T : ViewModel> create(modelClass: Class<T>): T =
                GuidanceViewModel(
                    repo = repository,
                    sampler = sampler,
                    cameraFactory = { owner, preview ->
                        GuidedCameraSource(
                            appContext,
                            owner,
                            DefaultCameraProviderSource(appContext),
                            poseEstimator,
                            { java.time.Instant.now() },
                        ).also { it.attachPreview(preview) }
                    },
                    prefs = guidedPrefs,
                    nanoClock = { System.nanoTime() },
                    utcClock = { java.time.Instant.now() },
                ) as T
        }

    fun sessionViewModelFactory(): ViewModelProvider.Factory {
        val container = this
        return object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T {
                require(modelClass.isAssignableFrom(SessionViewModel::class.java)) {
                    "unknown view model: ${modelClass.name}"
                }
                return SessionViewModel(
                    repo = container.repository,
                    files = container.fileStore,
                    sampler = container.sampler,
                    pairingStore = container.pairingStore,
                    prefs = container.samplingPrefs,
                    appVersion = BuildConfig.VERSION_NAME,
                    transportFactory = { baseUrl, tls -> HttpsTransport(baseUrl, tls) },
                ) as T
            }
        }
    }

    fun pairViewModelFactory(): ViewModelProvider.Factory {
        val container = this
        return object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T {
                require(modelClass.isAssignableFrom(PairViewModel::class.java)) {
                    "unknown view model: ${modelClass.name}"
                }
                return PairViewModel(
                    client = container.qrPairClient,
                    pairingStore = container.pairingStore,
                    deviceLabel = buildDeviceLabel(Build.MANUFACTURER, Build.MODEL),
                ) as T
            }
        }
    }
}
