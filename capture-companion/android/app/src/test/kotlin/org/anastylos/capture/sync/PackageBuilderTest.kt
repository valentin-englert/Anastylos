package org.anastylos.capture.sync

import com.google.common.truth.Truth.assertThat
import java.time.Instant
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import org.anastylos.capture.domain.sha256Hex
import org.junit.Test

/**
 * Cross-language proof that the native builder speaks schema v1 exactly
 * like the PWA exporter.
 *
 * Golden regeneration recipe (deletes itself after running; see history):
 * a temporary vitest file built this fixture with the production
 * `planExport`/`buildPackage` — session "SNES teardown"
 * (11111111-…), two `camera`/`unreviewed` frame observations
 * (22222222-…222/223) with 16-byte JPEG stand-ins, a crop on frame 2,
 * package id 44444444-…, exported_at 2026-10-27T09:01:00.000Z —
 * and wrote `manifest.json` + `package.zip` under
 * `src/test/resources/golden/frame-session/`.
 */
class PackageBuilderTest {
    // Fixture builders live in GoldenFrameSession, shared with the Slice A
    // demo harness so the emitted ZIP can never drift from these inputs.
    private val frame1Bytes get() = GoldenFrameSession.frame1Bytes
    private val frame2Bytes get() = GoldenFrameSession.frame2Bytes

    private fun session() = GoldenFrameSession.session()

    private fun goldenObservations() = GoldenFrameSession.goldenObservations()

    private fun goldenBytes(name: String): ByteArray =
        requireNotNull(javaClass.getResource("/golden/frame-session/$name"))
            .readBytes()

    @Test
    fun `manifest bytes match the PWA golden`() {
        val built = buildFramePackage(
            session = session(),
            observations = goldenObservations(),
            resolveOriginal = { id ->
                when (id) {
                    "33333333-3333-4333-8333-333333333333" -> frame1Bytes
                    "33333333-3333-4333-8333-333333333334" -> frame2Bytes
                    else -> null
                }
            },
            appVersion = "0.1.0",
            packageId = "44444444-4444-4444-8444-444444444444",
            exportedAt = Instant.parse("2026-10-27T09:01:00.000Z"),
        )
        // The golden file may check out with CRLF; the PWA spells LF.
        val golden = goldenBytes("manifest.json").toString(Charsets.UTF_8).replace("\r\n", "\n")
        assertThat(built.manifestJson).doesNotContain("\r")
        assertThat(built.manifestJson).isEqualTo(golden)
    }

    @Test
    fun `zip structure matches the PWA golden`() {
        val built = buildFramePackage(
            session = session(),
            observations = goldenObservations(),
            resolveOriginal = {
                if (it.endsWith("3333")) frame1Bytes else frame2Bytes
            },
            appVersion = "0.1.0",
            packageId = "44444444-4444-4444-8444-444444444444",
            exportedAt = Instant.parse("2026-10-27T09:01:00.000Z"),
            mtimeMillis = Instant.parse("2026-10-27T09:01:00.000Z").toEpochMilli(),
        )
        fun structure(bytes: ByteArray): List<Triple<String, Int, Long>> {
            val out = mutableListOf<Triple<String, Int, Long>>()
            ZipInputStream(bytes.inputStream()).use { zip ->
                var entry = zip.nextEntry
                while (entry != null) {
                    val data = zip.readBytes()
                    val crc = java.util.zip.CRC32().also { it.update(data) }.value
                    out += Triple(entry.name, entry.method, entry.size shl 32 or crc)
                    zip.closeEntry()
                    entry = zip.nextEntry
                }
            }
            return out
        }
        val mine = structure(built.bytes)
        val theirs = structure(goldenBytes("package.zip"))
        assertThat(mine.map { it.first }).isEqualTo(theirs.map { it.first })
        assertThat(mine.map { it.second }).containsExactlyElementsIn(theirs.map { it.second })
        assertThat(mine.map { it.third }).containsExactlyElementsIn(theirs.map { it.third })
        assertThat(mine.map { it.second }.toSet()).containsExactly(ZipEntry.STORED)

        fun member(bytes: ByteArray, name: String): ByteArray {
            ZipInputStream(bytes.inputStream()).use { zip ->
                var entry = zip.nextEntry
                while (entry != null) {
                    if (entry.name == name) return zip.readBytes()
                    zip.closeEntry()
                    entry = zip.nextEntry
                }
            }
            throw AssertionError("missing $name")
        }
        assertThat(member(built.bytes, "annotations.json"))
            .isEqualTo(member(goldenBytes("package.zip"), "annotations.json"))
    }

    @Test
    fun `build is deterministic for fixed inputs`() {
        fun build() = buildFramePackage(
            session = session(),
            observations = goldenObservations(),
            resolveOriginal = {
                if (it.endsWith("3333")) frame1Bytes else frame2Bytes
            },
            appVersion = "0.1.0",
            packageId = "44444444-4444-4444-8444-444444444444",
            exportedAt = Instant.parse("2026-10-27T09:01:00.000Z"),
            mtimeMillis = Instant.parse("2026-10-27T09:01:00.000Z").toEpochMilli(),
        )
        val first = build()
        val second = build()
        assertThat(first.manifestJson).isEqualTo(second.manifestJson)
        assertThat(first.bytes).isEqualTo(second.bytes)
        assertThat(first.packageSha256).isEqualTo(first.bytes.sha256Hex())
    }

    @Test
    fun `plan refuses imageless observations`() {
        val bare = goldenObservations().first().copy(images = emptyList())
        val issues = planFramePackage(session(), listOf(bare), { null })
        assertThat(issues).isNotEmpty()
        try {
            buildFramePackage(session(), listOf(bare), { null }, "0.1.0")
            throw AssertionError("expected PackageBuildException")
        } catch (e: PackageBuildException) {
            assertThat(e.message).contains("Export blocked")
        }
    }

    @Test
    fun `plan re-hashes originals and refuses tampering`() {
        val obs = goldenObservations().first()
        val tampered = frame1Bytes.copyOf().also { it[0] = (it[0] + 1).toByte() }
        val issues = planFramePackage(session(), listOf(obs), { tampered })
        assertThat(issues.map { it.message }.joinToString(" ")).contains("hash check")
    }

    @Test
    fun `plan refuses missing originals and foreign sessions`() {
        val obs = goldenObservations().first()
        assertThat(planFramePackage(session(), listOf(obs), resolveOriginal = { null })).isNotEmpty()

        val foreign = obs.copy(sessionId = "99999999-9999-4999-8999-999999999999")
        val foreignIssues =
            planFramePackage(session(), listOf(foreign), resolveOriginal = { frame1Bytes })
        assertThat(foreignIssues.map { it.message }.joinToString(" ")).contains("another session")
    }

    @Test
    fun `plan refuses bad package ids and empty selections`() {
        val obs = goldenObservations().first()
        assertThat(planFramePackage(session(), listOf(obs), { frame1Bytes }, packageId = "nope"))
            .isNotEmpty()
        assertThat(planFramePackage(session(), emptyList(), { frame1Bytes })).isNotEmpty()
    }

    @Test
    fun `oversized manifests are refused`() {
        val huge = goldenObservations().first().copy(operatorNotes = "x".repeat(6 * 1024 * 1024))
        try {
            buildFramePackage(session(), listOf(huge), { frame1Bytes }, "0.1.0")
            throw AssertionError("expected PackageBuildException")
        } catch (e: PackageBuildException) {
            assertThat(e.message).contains("5 MiB")
        }
    }

    @Test
    fun `previews ride along when resolved`() {
        val preview = "preview-bytes".toByteArray(Charsets.UTF_8)
        val built = buildFramePackage(
            session = session(),
            observations = listOf(goldenObservations().first()),
            resolveOriginal = { frame1Bytes },
            appVersion = "0.1.0",
            resolvePreview = { PreviewBytes("image/jpeg", preview) },
        )
        assertThat(built.manifestJson).contains("previews/22222222-2222-4222-8222-222222222222/")
        assertThat(built.manifestJson).contains("\"kind\": \"preview\"")
    }
}
