package org.anastylos.capture.sync

import java.time.Instant
import org.anastylos.capture.domain.CaptureSession
import org.anastylos.capture.domain.CaptureSource
import org.anastylos.capture.domain.ComponentObservation
import org.anastylos.capture.domain.ConfirmedField
import org.anastylos.capture.domain.CropRect
import org.anastylos.capture.domain.EpistemicState
import org.anastylos.capture.domain.EvidenceImage
import org.anastylos.capture.domain.ImageRole
import org.anastylos.capture.domain.ReviewState
import org.anastylos.capture.domain.sha256Hex

/**
 * The deterministic 2-frame session both the golden tests and the Slice A
 * demo harness build from. One definition so the emitted demo ZIP can never
 * drift from the inputs the golden proof pins down; plus a second,
 * single-frame session the harness uses for session-isolation checks.
 */
object GoldenFrameSession {
    const val PACKAGE_ID = "44444444-4444-4444-8444-444444444444"
    const val EXPORTED_AT = "2026-10-27T09:01:00.000Z"
    const val APP_VERSION = "0.1.0"
    const val SECOND_PACKAGE_ID = "55555555-5555-4555-8555-555555555555"
    const val SECOND_EXPORTED_AT = "2026-10-27T09:06:00.000Z"
    const val SECOND_SESSION_ID = "99999999-9999-4999-8999-999999999999"

    val frame1Bytes = "frame-0001-bytes".toByteArray(Charsets.UTF_8)
    val frame2Bytes = "frame-0002-bytes".toByteArray(Charsets.UTF_8)
    val secondFrameBytes = "second-session-frame".toByteArray(Charsets.UTF_8)

    fun session() = CaptureSession(
        sessionId = "11111111-1111-4111-8111-111111111111",
        name = "SNES teardown",
        createdAt = Instant.parse("2026-10-27T09:00:00.000Z"),
        updatedAt = Instant.parse("2026-10-27T09:00:00.000Z"),
        assemblyMemorySession = null,
        trayDefault = null,
    )

    fun secondSession() = session().copy(
        sessionId = SECOND_SESSION_ID,
        name = "Microscope detail",
        createdAt = Instant.parse("2026-10-27T09:05:00.000Z"),
        updatedAt = Instant.parse("2026-10-27T09:05:00.000Z"),
    )

    fun frameImage(
        imageId: String,
        observationId: String,
        filename: String,
        capturedAt: Instant,
        bytes: ByteArray,
        crop: CropRect?,
    ) = EvidenceImage(
        imageId = imageId,
        observationId = observationId,
        role = ImageRole.OTHER,
        filename = filename,
        mime = "image/jpeg",
        byteLength = bytes.size.toLong(),
        sha256 = bytes.sha256Hex(),
        capturedAt = capturedAt,
        crop = crop,
    )

    fun frameObservation(
        sessionId: String,
        observationId: String,
        capturedAt: Instant,
        image: EvidenceImage,
    ) = ComponentObservation(
        observationId = observationId,
        sessionId = sessionId,
        componentReference = null,
        trayReference = null,
        capturedAt = capturedAt,
        captureSource = CaptureSource.CAMERA,
        reviewState = ReviewState.UNREVIEWED,
        operatorNotes = null,
        markings = emptyList(),
        category = ConfirmedField(null, EpistemicState.UNKNOWN),
        measurements = emptyList(),
        quantity = ConfirmedField(null, EpistemicState.UNKNOWN),
        candidates = emptyList(),
        // Capture-time version, preserved verbatim like the PWA does; the
        // golden fixture was captured as "dev" and exported as "0.1.0".
        appVersion = "dev",
        userAgent = null,
        exportHistory = emptyList(),
        images = listOf(image),
    )

    fun goldenObservations(): List<ComponentObservation> {
        val sessionId = session().sessionId
        val img1 = frameImage(
            "33333333-3333-4333-8333-333333333333",
            "22222222-2222-4222-8222-222222222222",
            "frame-0001.jpg",
            Instant.parse("2026-10-27T09:00:05.000Z"),
            frame1Bytes,
            null,
        )
        val img2 = frameImage(
            "33333333-3333-4333-8333-333333333334",
            "22222222-2222-4222-8222-222222222223",
            "frame-0002.jpg",
            Instant.parse("2026-10-27T09:00:10.000Z"),
            frame2Bytes,
            CropRect(0.1, 0.2, 0.5, 0.4),
        )
        return listOf(
            frameObservation(sessionId, img1.observationId, img1.capturedAt, img1),
            frameObservation(sessionId, img2.observationId, img2.capturedAt, img2),
        )
    }

    fun secondSessionObservations(): List<ComponentObservation> {
        val img = frameImage(
            "77777777-7777-4777-8777-777777777777",
            "88888888-8888-4888-8888-888888888888",
            "frame-0001.jpg",
            Instant.parse("2026-10-27T09:05:05.000Z"),
            secondFrameBytes,
            null,
        )
        return listOf(frameObservation(SECOND_SESSION_ID, img.observationId, img.capturedAt, img))
    }
}
