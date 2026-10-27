# ANASTYLOS Capture ProGuard rules. Room, CameraX, Navigation and ML Kit ship
# their own consumer rules; only app-owned serialized names are kept here.
-keepnames class org.anastylos.capture.sync.** { *; }
-keepnames class org.anastylos.capture.evidence.** { *; }
