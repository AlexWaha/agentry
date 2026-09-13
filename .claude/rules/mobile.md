# Mobile Development Standards

> **[OPTIONAL - mobile projects only]** Skip this rule if the project is web-only.

Standards for mobile application development. When the project includes a mobile app, it is treated as a first-class deliverable, not a secondary afterthought.

---

## Technology

- **Mobile framework:** React Native + TypeScript, Flutter + Dart, native iOS (Swift) / Android (Kotlin), or hybrid (Capacitor / Ionic). The choice is made at architecture phase based on feature requirements, team skills, and code-reuse goals.
- **Platform decision sub-choices** (e.g. Expo vs bare React Native for RN projects) are deferred to the Architect's recommendation.
- **Minimum platform versions:** define explicitly per project (typical modern baseline: iOS 15+, Android 10+).

## Code Sharing Strategy

Maximize code reuse between web and mobile where the chosen stack allows (strongest with React + React Native, weaker with native iOS/Android, varies with Flutter).

### Shared (Web + Mobile)

- **Business logic** - hooks, state management, API client, validation
- **Types and interfaces** - language-level types, DTOs, enums
- **API layer** - HTTP client, request/response types, error handling
- **State management** - stores, reducers, selectors
- **Utilities** - date formatting, string helpers, calculations

### Platform-Specific

- **UI components** - rendering primitives differ between web and mobile
- **Navigation** - mobile navigation stacks vs web routers
- **Storage** - mobile secure storage / key-value stores vs browser storage
- **Notifications** - push notifications are mobile-only
- **Camera/Scanner** - mobile-only features
- **Haptic feedback** - mobile-only

### Package Structure

`[EXAMPLE - React Native monorepo]` Adapt to your chosen stack. Flutter projects use a single app codebase; native projects use separate repos or platform folders.

```
project/app/frontend/
├── packages/
│   └── shared/           # Shared business logic, types, API client
├── web/                  # Web application
└── mobile/               # Mobile application
```

## Offline-First Architecture

The mobile app MUST work offline for core read/write flows. Users should be able to:
- View their primary data without internet connection
- Perform common mutations offline and have them queued for sync
- Scan codes / capture data offline and queue results for sync

### Implementation Pattern

1. **Local storage** - all active data cached locally (SQLite, MMKV, Core Data, Room, Hive, etc.)
2. **Sync queue** - mutations are queued when offline
3. **Background sync** - queue is processed when connection is restored
4. **Conflict resolution** - last-write-wins for simple fields, merge for collections
5. **Sync status indicator** - user sees when data is unsynced

```
User Action → Local State Update → Queue Mutation → [Online?] → Sync to Server
                                                      ↓ [Offline]
                                                  Store in Queue → Retry on Connect
```

## Push Notifications

| Platform | Service | Typical Library |
|----------|---------|-----------------|
| Android | Firebase Cloud Messaging (FCM) | Framework-specific FCM SDK |
| iOS | Apple Push Notification service (APNs) | FCM wrapper or native APNs SDK |

### Notification Types

Define per project. Common categories:
- **Reminder notifications** - time-based prompts tied to user data
- **Engagement notifications** - streaks, achievements, progress
- **Transactional notifications** - confirmations, status changes
- **Sync notifications** - background sync completion

### Requirements

- User must explicitly opt-in to notifications
- Notification preferences must be granular (per type)
- Notification scheduling must respect user's timezone
- Silent notifications for background sync

## Deep Linking

Deep links from external sources (QR codes, email, web, other apps) must:

1. Open the specific resource in the app
2. If app not installed - redirect to App Store / Google Play
3. If not logged in - deep link is preserved through login flow
4. Support any parameters the flow needs (referral codes, share tokens, etc.)

### URL Scheme Example

```
app://<resource>/{resource-id}
app://referral/{referral-code}
https://app.example.com/<resource>/{resource-id}   # Universal link fallback
```

## Camera / Scanner

When the product requires camera access, support the relevant capabilities:

1. **QR / barcode scanning** - read codes from printed material
2. **Document / image capture** - photograph real-world artifacts for processing
3. **Recognition features** (OCR, handwriting, object detection) - future or project-specific

Camera access requires explicit permission request with clear explanation of why it's needed.

## App Store Guidelines

### Both Platforms

- App description, screenshots, and metadata in English (primary) + target languages
- Privacy policy URL required
- No misleading claims about functionality
- Subscription pricing clearly disclosed before purchase
- Free trial terms clearly stated

### iOS Specific

- In-app purchases via StoreKit 2 (Apple mandate)
- No external payment links in the app
- Review guidelines compliance: no hidden features, no private API usage

### Android Specific

- Google Play Billing Library for subscriptions
- Target latest Android SDK version
- Material Design guidelines where applicable
- Adaptive icons

## Performance Standards

- **Cold start** - under 2 seconds on mid-range devices
- **Screen transitions** - under 300ms
- **List scrolling** - 60fps with no jank
- **Bundle size** - minimize, use code splitting / tree-shaking
- **Memory** - no memory leaks in long sessions
- **Battery** - background sync must be battery-efficient (WorkManager on Android, BGTaskScheduler on iOS, platform equivalents elsewhere)

## Testing

- **Unit tests** - framework-appropriate runner for business logic (Jest for RN, Flutter's built-in test runner, XCTest, JUnit)
- **Component / widget tests** - React Native Testing Library, Flutter widget tests, SwiftUI previews, Compose UI tests
- **E2E tests** - Detox, Maestro, Appium, or Flutter integration tests
- **Device testing** - test on at least 3 screen sizes (small phone, regular phone, tablet)
- **Platform testing** - test on both iOS and Android before every release

## Accessibility

- Screen reader support (VoiceOver / TalkBack)
- Minimum touch target size: 44×44pt (iOS) / 48×48dp (Android)
- Color contrast ratio: minimum 4.5:1 for text
- Support for dynamic font sizes
- All images have alt text / accessibility labels
