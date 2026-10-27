package org.anastylos.capture.sync

import com.google.common.truth.Truth.assertThat
import java.io.File
import java.time.Instant
import org.anastylos.capture.domain.sha256Hex
import org.junit.Test

/**
 * JVM half of the Slice A in-session demo: builds the deterministic
 * Kotlin frame packages and emits them under `build/outputs/demo/` (or
 * `-Danastylos.demoOut=…`), where the Python half of the demo feeds them
 * to the unchanged validator and the receiver staging path (see
 * `receiver/SESSION_FEED.md`).
 *
 * The asserts earn this file its place as a regression test: clean plan,
 * `captured_at` ordering even when frames arrive out of order (delayed
 * frames), manifest/asset/hash structure, and byte-determinism. The file
 * emission is a side effect into gitignored `build/` only.
 */
class DemoHarnessTest {
    private val demoDir: File
        get() = File(System.getProperty("anastylos.demoOut", "build/outputs/demo"))

    private fun emit(name: String, built: BuiltFramePackage): File {
        val out = demoDir.resolve(name)
        out.parentFile.mkdirs()
        out.writeBytes(built.bytes)
        println("demo-package: $name package_id=${built.packageId} sha256=${built.packageSha256}")
        return out
    }

    @Test
    fun `golden frame package is clean, ordered and deterministic`() {
        val session = GoldenFrameSession.session()
        val observations = GoldenFrameSession.goldenObservations()
        val resolveOriginal = { id: String ->
            when (id) {
                "33333333-3333-4333-8333-333333333333" -> GoldenFrameSession.frame1Bytes
                "33333333-3333-4333-8333-333333333334" -> GoldenFrameSession.frame2Bytes
                else -> null
            }
        }
        assertThat(planFramePackage(session, observations, resolveOriginal, GoldenFrameSession.PACKAGE_ID))
            .isEmpty()

        fun build(input: List<org.anastylos.capture.domain.ComponentObservation>) = buildFramePackage(
            session = session,
            observations = input,
            resolveOriginal = resolveOriginal,
            appVersion = GoldenFrameSession.APP_VERSION,
            packageId = GoldenFrameSession.PACKAGE_ID,
            exportedAt = Instant.parse(GoldenFrameSession.EXPORTED_AT),
            mtimeMillis = Instant.parse(GoldenFrameSession.EXPORTED_AT).toEpochMilli(),
        )

        // Delayed frames: reversed arrival still yields captured_at order.
        val fromReversed = build(observations.reversed())
        val firstIdx = fromReversed.manifestJson.indexOf("22222222-2222-4222-8222-222222222222")
        val secondIdx = fromReversed.manifestJson.indexOf("22222222-2222-4222-8222-222222222223")
        assertThat(firstIdx).isGreaterThan(-1)
        assertThat(secondIdx).isGreaterThan(firstIdx)
        assertThat(fromReversed.observationIds).containsExactly(
            "22222222-2222-4222-8222-222222222222",
            "22222222-2222-4222-8222-222222222223",
        ).inOrder()

        val built = build(observations)
        assertThat(built.bytes).isEqualTo(fromReversed.bytes)
        assertThat(built.packageSha256).isEqualTo(built.bytes.sha256Hex())
        // manifest + 2 originals + annotations (crop on frame 2).
        assertThat(built.fileCount).isEqualTo(4)
        assertThat(built.manifestJson).contains(session.sessionId)
        assertThat(built.manifestJson).contains("\"capture_source\": \"camera\"")
        assertThat(built.manifestJson).contains("\"review_state\": \"unreviewed\"")

        val out = emit("frame-session.zip", built)
        assertThat(out.isFile).isTrue()
        assertThat(out.length()).isEqualTo(built.bytes.size.toLong())
    }

    @Test
    fun `second session package is single-session and deterministic`() {
        val session = GoldenFrameSession.secondSession()
        val observations = GoldenFrameSession.secondSessionObservations()
        val built = buildFramePackage(
            session = session,
            observations = observations,
            resolveOriginal = { GoldenFrameSession.secondFrameBytes },
            appVersion = GoldenFrameSession.APP_VERSION,
            packageId = GoldenFrameSession.SECOND_PACKAGE_ID,
            exportedAt = Instant.parse(GoldenFrameSession.SECOND_EXPORTED_AT),
            mtimeMillis = Instant.parse(GoldenFrameSession.SECOND_EXPORTED_AT).toEpochMilli(),
        )
        assertThat(built.observationIds).containsExactly("88888888-8888-4888-8888-888888888888")
        assertThat(built.manifestJson).contains(GoldenFrameSession.SECOND_SESSION_ID)
        assertThat(built.manifestJson).doesNotContain(GoldenFrameSession.session().sessionId)
        assertThat(built.packageSha256).isEqualTo(built.bytes.sha256Hex())
        // manifest + 1 original, no annotations.
        assertThat(built.fileCount).isEqualTo(2)

        val again = buildFramePackage(
            session = session,
            observations = observations,
            resolveOriginal = { GoldenFrameSession.secondFrameBytes },
            appVersion = GoldenFrameSession.APP_VERSION,
            packageId = GoldenFrameSession.SECOND_PACKAGE_ID,
            exportedAt = Instant.parse(GoldenFrameSession.SECOND_EXPORTED_AT),
            mtimeMillis = Instant.parse(GoldenFrameSession.SECOND_EXPORTED_AT).toEpochMilli(),
        )
        assertThat(again.bytes).isEqualTo(built.bytes)

        emit("frame-session-second.zip", built)
    }
}
