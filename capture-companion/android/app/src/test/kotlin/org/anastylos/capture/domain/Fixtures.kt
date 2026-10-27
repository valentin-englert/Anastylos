package org.anastylos.capture.domain

import java.time.Instant

/** Shared builders for domain tests. Every value here is valid by construction. */
object Fixtures {
    const val SESSION_ID = "11111111-1111-4111-8111-111111111111"
    const val OBS_ID = "22222222-2222-4222-8222-222222222222"
    const val IMAGE_ID = "33333333-3333-4333-8333-333333333333"
    const val STAMP = "2026-01-15T10:30:00.000Z"

    fun instant(): Instant = Instant.parse(STAMP)

    fun session() = CaptureSession(
        sessionId = SESSION_ID,
        name = "Day 1 teardown",
        createdAt = instant(),
        updatedAt = instant(),
        assemblyMemorySession = null,
        trayDefault = null,
    )

    fun image() = EvidenceImage(
        imageId = IMAGE_ID,
        observationId = OBS_ID,
        role = ImageRole.FRONT,
        filename = "front.jpg",
        mime = "image/jpeg",
        byteLength = 259,
        sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        capturedAt = instant(),
        crop = null,
    )

    fun observation() = ComponentObservation(
        observationId = OBS_ID,
        sessionId = SESSION_ID,
        componentReference = null,
        trayReference = null,
        capturedAt = instant(),
        captureSource = CaptureSource.CAMERA,
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
        images = listOf(image()),
    )
}
