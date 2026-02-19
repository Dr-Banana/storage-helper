# Android Development Setup Guide

This guide covers how to connect your Android phone via ADB reverse, build the app, and run it every time you do local development.

---

## Prerequisites

- Android Studio installed (includes ADB in `platform-tools`)
- USB cable or Wi-Fi ADB (Android 11+)
- Developer Options + USB Debugging enabled on your phone
- Docker backend services running locally (ports `8000` and `8888`)
- Node.js dependencies installed: `npm install` inside `StorageHelperWebService/`

---

## Step 1 — Connect Your Phone via ADB

### Option A: USB Cable (most reliable)

1. Plug in your phone via USB.
2. On your phone, tap **"Allow USB Debugging"** when prompted.
3. Verify the device is detected:

```bash
adb devices
```

Expected output:
```
List of devices attached
XXXXXXXXXXXXXXXX    device
```

If it shows `unauthorized`, unlock your phone and tap **Allow** again.

---

### Option B: Wi-Fi (Android 11+, wireless)

1. Connect phone and PC to the **same Wi-Fi network**.
2. On the phone: go to **Developer Options → Wireless Debugging → Pair device with pairing code**.
3. Note the IP address and pairing port shown on the screen, then run:

```bash
adb pair <phone-ip>:<pairing-port>
# Example: adb pair 192.168.1.42:37829
# Enter the 6-digit pairing code when prompted
```

4. Then connect:

```bash
adb connect <phone-ip>:<connection-port>
# The connection port is shown under "Wireless Debugging" (different from pairing port)
# Example: adb connect 192.168.1.42:40001
```

5. Verify:

```bash
adb devices
```

---

## Step 2 — Set Up ADB Reverse (Port Forwarding)

This makes the **phone's localhost** point to your **PC's localhost**, so the app can reach the Docker backend services running on your machine.

Run these two commands every time you start a dev session:

```bash
adb reverse tcp:8000 tcp:8000
adb reverse tcp:8888 tcp:8888
```

- Port `8000` → Storage Data Service (`VITE_API_BASE_URL`)
- Port `8888` → AI Orchestra Service (`VITE_AI_ORCHESTRA_URL`)

Verify reverse tunnels are active:

```bash
adb reverse --list
```

Expected output:
```
(reverse) tcp:8000  tcp:8000
(reverse) tcp:8888  tcp:8888
```

> **Note:** ADB reverse rules are lost when the phone disconnects or reboots. You must re-run these commands each time.

---

## Step 3 — Build the Web App for Android (Dev Mode)

The Android app is a Capacitor wrapper around the React/Vite web app. Build it with the development environment so it points to `http://127.0.0.1:8000` (forwarded from your PC):

```bash
cd StorageHelperWebService
npm run android:dev
```

This command runs:
1. `tsc` — TypeScript compile check
2. `vite build --mode development` — builds with `.env.development` (local API URLs)
3. `npx cap sync android` — syncs the built `dist/` into the Android project

> **For production build** (uses the hosted Render.com APIs instead of local):
> ```bash
> npm run android:prod
> ```

---

## Step 4 — Run on Device via Android Studio

1. Open Android Studio.
2. Open project: `StorageHelperWebService/android/`
3. Select your connected phone in the device dropdown (top toolbar).
4. Click **Run ▶** (or press `Shift+F10`).

Android Studio will build the APK, install it on your phone, and launch it.

---

## Full Checklist (Every Dev Session)

Run through this list each time you want to test on your phone:

```
[ ] 1. Start Docker backend services (ports 8000 and 8888)
[ ] 2. Connect phone via USB or Wi-Fi ADB
[ ] 3. adb reverse tcp:8000 tcp:8000
[ ] 4. adb reverse tcp:8888 tcp:8888
[ ] 5. cd StorageHelperWebService && npm run android:dev
[ ] 6. Open Android Studio → android/ → Run on device
```

---

## Troubleshooting

### `adb: no devices/emulators found`
- Replug the USB cable
- Re-enable USB Debugging on the phone
- Try: `adb kill-server && adb start-server`

### App shows "Network Error" or can't reach the API
- Make sure `adb reverse` is set up (Step 2)
- Confirm Docker containers are running: `docker ps`
- Check that the build used `android:dev` (not `android:prod`)

### `adb reverse` list is empty after phone reconnect
- The phone disconnected — re-run Step 1 and Step 2

### Build fails with TypeScript errors
- Run `npm install` first
- Check for type errors: `npx tsc --noEmit`

### App installs but shows blank screen
- Check that `npm run android:dev` completed without errors
- Verify `dist/` folder was generated in `StorageHelperWebService/`
- In Android Studio, check **Logcat** for JS errors
