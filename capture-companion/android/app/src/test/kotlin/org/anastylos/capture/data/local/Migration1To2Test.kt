package org.anastylos.capture.data.local

import android.database.sqlite.SQLiteDatabase
import androidx.room.Room
import com.google.common.truth.Truth.assertThat
import java.time.Instant
import kotlinx.coroutines.test.runTest
import org.anastylos.capture.data.GuidedAssociation
import org.anastylos.capture.guidance.DeliveryState
import org.anastylos.capture.guidance.ReceiptKind
import org.junit.After
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * Forward-only v1 → v2 migration against a real pre-upgrade
 * database file. The v1 DDL below is frozen from
 * `schemas/.../CaptureDatabase/1.json` (identity
 * `e52c19f3e15ea1c9ec921fd6146a187b`): if v1 ever changes, this
 * fixture — not the migration — is what must be revisited.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class Migration1To2Test {
    private val dbName = "migration-1-2-test.db"
    private var db: CaptureDatabase? = null

    @After
    fun tearDown() {
        db?.close()
        db = null
        RuntimeEnvironment.getApplication().getDatabasePath(dbName).delete()
    }

    @Test
    fun `v1 evidence survives and v2 links work`() = runTest {
        createV1Database(
            sessionId = "s-1",
            observationId = "o-1",
            imageId = "i-1",
            imageSha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

        val context = RuntimeEnvironment.getApplication()
        val migrated = Room.databaseBuilder(context, CaptureDatabase::class.java, dbName)
            .addMigrations(MIGRATION_1_2)
            .build()
        db = migrated

        // Room validates the migrated schema on open; reaching here
        // proves the migration SQL matches the v2 entities.
        val session = migrated.sessions().getById("s-1")
        assertThat(session?.name).isEqualTo("pre-upgrade session")
        val observation = migrated.observations().getById("o-1")
        assertThat(observation?.sessionId).isEqualTo("s-1")
        assertThat(observation?.exportHistoryJson).isEqualTo("[]")
        val image = migrated.images().getById("i-1")
        assertThat(image?.sha256)
            .isEqualTo("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

        val version = migrated.openHelper.readableDatabase.query("PRAGMA user_version")
        version.moveToFirst()
        assertThat(version.getInt(0)).isEqualTo(2)
        version.close()

        // The new link table works on the migrated file.
        val stamp = Instant.parse("2026-10-29T12:00:00Z")
        migrated.guidedAssociations().upsert(
            GuidedAssociationEntity(
                imageId = "i-1",
                observationId = "o-1",
                packageId = "p-1",
                guidedSessionId = "g-1",
                requestId = "r-1",
                requestRevision = 1,
                attemptId = "a-1",
                attemptNumber = 1,
                sidecarRevision = 0,
                deliveryState = DeliveryState.PACKAGE_ACCEPTED.name,
                receiptStatus = ReceiptKind.PACKAGE_ONLY.wire,
                updatedAt = stamp,
            ),
        )
        val link = migrated.guidedAssociations().getByImageId("i-1")
        assertThat(link?.attemptId).isEqualTo("a-1")
        assertThat(migrated.guidedAssociations().getByAttemptId("a-1")?.imageId)
            .isEqualTo("i-1")

        // Deleting the image cascades the link, never the reverse.
        migrated.images().deleteById("i-1")
        assertThat(migrated.guidedAssociations().getByImageId("i-1")).isNull()
    }

    @Test
    fun `unknown stored states fail closed at the boundary`() = runTest {
        createV1Database(sessionId = "s-2", observationId = "o-2", imageId = "i-2", imageSha = "aa")
        val context = RuntimeEnvironment.getApplication()
        val migrated = Room.databaseBuilder(context, CaptureDatabase::class.java, dbName)
            .addMigrations(MIGRATION_1_2)
            .build()
        db = migrated

        val stamp = Instant.parse("2026-10-29T12:00:00Z")
        migrated.guidedAssociations().upsert(
            GuidedAssociationEntity(
                imageId = "i-2",
                observationId = "o-2",
                packageId = null,
                guidedSessionId = "g-2",
                requestId = null,
                requestRevision = null,
                attemptId = null,
                attemptNumber = null,
                sidecarRevision = 0,
                deliveryState = "BOGUS_STATE",
                receiptStatus = null,
                updatedAt = stamp,
            ),
        )
        // The DAO stores raw strings; the repository refuses to
        // interpret an unknown spelling as a known state.
        val repository = org.anastylos.capture.data.RoomEvidenceRepository(
            migrated,
            object : org.anastylos.capture.data.EvidenceFileStorePort {
                override fun deleteObservationFiles(observationId: String) = Unit
            },
            { stamp },
            "0.1.0-test",
        )
        var thrown = false
        try {
            repository.getGuidedAssociation("i-2")
        } catch (_: IllegalStateException) {
            thrown = true
        }
        assertThat(thrown).isTrue()
    }

    @Test
    fun `negative revisions are refused`() = runTest {
        val context = RuntimeEnvironment.getApplication()
        val fresh = Room.inMemoryDatabaseBuilder(context, CaptureDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        db = fresh
        val stamp = Instant.parse("2026-10-29T12:00:00Z")
        val repository = org.anastylos.capture.data.RoomEvidenceRepository(
            fresh,
            object : org.anastylos.capture.data.EvidenceFileStorePort {
                override fun deleteObservationFiles(observationId: String) = Unit
            },
            { stamp },
            "0.1.0-test",
        )
        var thrown = false
        try {
            repository.recordGuidedAssociation(
                GuidedAssociation(
                    imageId = "i-9",
                    observationId = "o-9",
                    packageId = null,
                    guidedSessionId = "g-9",
                    requestId = null,
                    requestRevision = 0,
                    attemptId = null,
                    attemptNumber = null,
                    sidecarRevision = 0,
                    deliveryState = DeliveryState.PENDING_PACKAGE,
                    receiptStatus = null,
                    updatedAt = stamp,
                ),
            )
        } catch (_: IllegalArgumentException) {
            thrown = true
        }
        assertThat(thrown).isTrue()
    }

    private fun createV1Database(
        sessionId: String,
        observationId: String,
        imageId: String,
        imageSha: String,
    ) {
        val file = RuntimeEnvironment.getApplication().getDatabasePath(dbName)
        file.parentFile?.mkdirs()
        if (file.exists()) file.delete()
        val sqlite = SQLiteDatabase.openOrCreateDatabase(file, null)
        try {
            for (sql in V1_DDL) sqlite.execSQL(sql)
            sqlite.execSQL(
                "INSERT INTO sessions (session_id, name, created_at, updated_at, " +
                    "assembly_memory_session, tray_default) VALUES (?, ?, ?, ?, ?, ?)",
                arrayOf<Any?>(sessionId, "pre-upgrade session", STAMP, STAMP, null, null),
            )
            sqlite.execSQL(
                "INSERT INTO observations (observation_id, session_id, component_reference, " +
                    "tray_reference, captured_at, capture_source, review_state, operator_notes, " +
                    "markings_json, category_value, category_state, measurements_json, " +
                    "quantity_value, quantity_state, candidates_json, app_version, user_agent, " +
                    "export_history_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                arrayOf<Any?>(
                    observationId, sessionId, null, null, STAMP, "camera", "unreviewed",
                    null, "[]", null, "unknown", "[]", null, "unknown", "[]",
                    "0.1.0-test", null, "[]",
                ),
            )
            sqlite.execSQL(
                "INSERT INTO images (image_id, observation_id, role, filename, mime, " +
                    "byte_length, sha256, captured_at, crop_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                arrayOf<Any?>(imageId, observationId, "front", "front.jpg", "image/jpeg", 3, imageSha, STAMP, null),
            )
            sqlite.execSQL("PRAGMA user_version = 1")
        } finally {
            sqlite.close()
        }
    }

    companion object {
        private const val STAMP = "2026-01-15T10:30:00Z"

        private val V1_DDL = listOf(
            "CREATE TABLE IF NOT EXISTS `sessions` (`session_id` TEXT NOT NULL, `name` TEXT NOT NULL, `created_at` TEXT NOT NULL, `updated_at` TEXT NOT NULL, `assembly_memory_session` TEXT, `tray_default` TEXT, PRIMARY KEY(`session_id`))",
            "CREATE TABLE IF NOT EXISTS `observations` (`observation_id` TEXT NOT NULL, `session_id` TEXT NOT NULL, `component_reference` TEXT, `tray_reference` TEXT, `captured_at` TEXT NOT NULL, `capture_source` TEXT NOT NULL, `review_state` TEXT NOT NULL, `operator_notes` TEXT, `markings_json` TEXT NOT NULL, `category_value` TEXT, `category_state` TEXT NOT NULL, `measurements_json` TEXT NOT NULL, `quantity_value` INTEGER, `quantity_state` TEXT NOT NULL, `candidates_json` TEXT NOT NULL, `app_version` TEXT NOT NULL, `user_agent` TEXT, `export_history_json` TEXT NOT NULL, PRIMARY KEY(`observation_id`), FOREIGN KEY(`session_id`) REFERENCES `sessions`(`session_id`) ON UPDATE NO ACTION ON DELETE CASCADE )",
            "CREATE TABLE IF NOT EXISTS `images` (`image_id` TEXT NOT NULL, `observation_id` TEXT NOT NULL, `role` TEXT NOT NULL, `filename` TEXT NOT NULL, `mime` TEXT NOT NULL, `byte_length` INTEGER NOT NULL, `sha256` TEXT NOT NULL, `captured_at` TEXT NOT NULL, `crop_json` TEXT, PRIMARY KEY(`image_id`), FOREIGN KEY(`observation_id`) REFERENCES `observations`(`observation_id`) ON UPDATE NO ACTION ON DELETE CASCADE )",
            "CREATE INDEX IF NOT EXISTS `index_observations_session_id` ON `observations` (`session_id`)",
            "CREATE INDEX IF NOT EXISTS `index_images_observation_id` ON `images` (`observation_id`)",
            "CREATE TABLE IF NOT EXISTS room_master_table (id INTEGER PRIMARY KEY,identity_hash TEXT)",
            "INSERT OR REPLACE INTO room_master_table (id,identity_hash) VALUES(42, 'e52c19f3e15ea1c9ec921fd6146a187b')",
        )
    }
}
