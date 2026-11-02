package org.anastylos.capture.ui.onboarding

/** Plain-English explanations, shared by the first-run guide and every in-app (i) button. */
enum class HelpTopic(val title: String, val body: String) {
    Connect(
        "Connect to the workstation",
        "Tap the QR icon and scan the code shown by the workstation. Once connected, the phone shows “Connected” and receives photo requests.",
    ),
    Requests(
        "Photo requests",
        "The workstation asks for a view of a part. Hold the phone over the board and keep it still. The photo is taken automatically and sent. If it is blurry, you will see a retake hint.",
    ),
    Iso(
        "ISO",
        "How sensitive the camera is to light. Low values give clean images, high values brighten dark scenes but add noise. Use Auto unless the picture is too dark or too bright.",
    ),
    Speed(
        "Shutter speed",
        "How long the sensor collects light. Fast speeds (1/500) freeze motion, slow speeds (1/30) brighten the image but blur if the phone moves. Manual mode sets ISO and speed together.",
    ),
    Ev(
        "Exposure (EV)",
        "Makes the automatic exposure brighter (+) or darker (−). Useful when the board looks washed out or too dim.",
    ),
    Focus(
        "Focus",
        "Auto lets the phone decide. Manual locks the focus distance. Pick the distance to the board, then the image stays sharp while you hold still.",
    ),
    WhiteBalance(
        "White balance",
        "Corrects the colour of the light so white paper looks white. Auto works for most lamps. Choose a preset if colours look too warm or too blue.",
    ),
    Torch(
        "Flashlight",
        "Keeps the phone light on for dark workbenches. Avoid pointing it straight at glossy parts: it creates glare.",
    ),
    Menu(
        "More settings",
        "Choose the camera, a saved profile, zoom and picture sizes. You can also replay this guide from here.",
    ),
    Voice(
        "Talk to Muse",
        "Tap the Muse icon to turn on the microphone. Start a sentence with “Muse” and ask about the current work. Muse answers out loud. Tap again to turn the microphone off.",
    ),
}
