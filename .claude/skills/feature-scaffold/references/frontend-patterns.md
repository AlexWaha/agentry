# Frontend Patterns: Stack Examples

Reference examples for frontend feature scaffolding: web components, mobile
components, shared state/data fetching, the API client, offline-first sync,
push notifications, deep linking, and component tests. All snippets are
`[EXAMPLE - <stack>]` - adapt names and idioms to the project's actual stack
per `.claude/project/stack.md` and `.claude/project/architecture.md`.

## Web Components

[EXAMPLE - React 19 + TanStack Query]

```tsx
// Functional components with hooks
interface CalendarGridProps {
  calendar: Calendar;
  onDateClick: (date: string) => void;
  isEditable: boolean;
}

export const CalendarGrid: React.FC<CalendarGridProps> = ({
  calendar,
  onDateClick,
  isEditable,
}) => {
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  const handleDateClick = useCallback((date: string) => {
    setSelectedDate(date);
    onDateClick(date);
  }, [onDateClick]);

  return (
    <div className="calendar-grid">
      {calendar.entries.map((entry) => (
        <CalendarCell
          key={entry.id}
          entry={entry}
          isSelected={selectedDate === entry.date}
          onClick={() => handleDateClick(entry.date)}
          disabled={!isEditable}
        />
      ))}
    </div>
  );
};
```

Adapt the pattern to your framework: functional components + hooks in React,
`<script setup>` + composables in Vue 3, runes in Svelte 5, signals in
Angular 18/SolidJS. The principles are the same: declarative rendering,
typed props, isolated state.

## Mobile Components

[EXAMPLE - React Native + TypeScript]

```tsx
// Platform-specific when needed, shared when possible
import { StyleSheet, View, Text, TouchableOpacity } from 'react-native';

interface CalendarCellProps {
  entry: CalendarEntry;
  isSelected: boolean;
  onPress: () => void;
}

export const CalendarCell: React.FC<CalendarCellProps> = ({
  entry,
  isSelected,
  onPress,
}) => {
  return (
    <TouchableOpacity
      style={[styles.cell, isSelected && styles.selected]}
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={`${entry.label} - ${entry.date}`}
    >
      <Text style={styles.label}>{entry.label}</Text>
      {entry.completed && <CheckIcon />}
    </TouchableOpacity>
  );
};
```

## Shared State and Data Fetching

[EXAMPLE - React 19 + TanStack Query]

```tsx
// Hooks shared between web and mobile
export function useCalendars() {
  const [calendars, setCalendars] = useState<Calendar[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchCalendars = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiClient.get<PaginatedResponse<Calendar>>('/api/v1/calendars');
      setCalendars(data.data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch calendars');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchCalendars();
  }, [fetchCalendars]);

  return { calendars, loading, error, refetch: fetchCalendars };
}
```

In Vue/Svelte/Angular, achieve the same via composables/stores/services with
the framework's native reactivity primitives.

## API Client

[EXAMPLE - fetch + Bearer {{AUTH_METHOD}}]

```tsx
// Typed API client with interceptors
const apiClient = {
  async get<T>(url: string, params?: Record<string, string>): Promise<T> {
    const response = await fetch(`${BASE_URL}${url}?${new URLSearchParams(params)}`, {
      headers: {
        'Authorization': `Bearer ${getToken()}`,
        'Accept': 'application/json',
      },
    });
    if (!response.ok) throw new ApiError(response);
    return response.json();
  },
};
```

## Shared Code Strategy

```
frontend/
├── packages/
│   └── shared/              # Shared between web and mobile
│       ├── api/             # API client, types, endpoints
│       ├── hooks/           # Custom hooks / composables / stores
│       ├── types/           # TypeScript interfaces and types
│       ├── utils/           # Utility functions
│       └── constants/       # Shared constants
├── web/                     # Web app
│   ├── src/
│   │   ├── components/      # Web-specific components
│   │   ├── pages/           # Page components (routes)
│   │   ├── layouts/         # Layout components
│   │   └── styles/          # Tailwind/CSS
│   └── tests/
└── mobile/                  # Mobile app
    ├── src/
    │   ├── components/      # Mobile-specific components
    │   ├── screens/         # Screen components (navigation)
    │   ├── navigation/      # Navigation setup
    │   └── services/        # Push notifications, offline sync
    └── __tests__/
```

## Offline-First Architecture

- Local storage (AsyncStorage / IndexedDB / MMKV) for offline data
- Sync queue: queue mutations when offline, replay when online
- Conflict resolution: last-write-wins with server timestamp
- Optimistic UI updates with rollback on sync failure
- Network status monitoring and user feedback

## Push Notifications

- FCM (Firebase Cloud Messaging) for Android
- APNs (Apple Push Notification Service) for iOS
- Notification categories: reminders, progress updates, social (referral)
- Permission request flow: explain value before requesting
- Notification preferences UI

## Deep Linking

- QR codes on printed materials link to specific resources
- URL scheme: `myapp://resource/{id}` for mobile app
- Universal links / App links for seamless web-to-app transition
- Referral attribution via URL parameters

## Component Tests

[EXAMPLE - React Testing Library + Vitest]

```tsx
describe('CalendarGrid', () => {
  it('renders all calendar entries', () => {
    const calendar = createMockCalendar({ entryCount: 7 });
    const { getAllByRole } = render(
      <CalendarGrid calendar={calendar} onDateClick={jest.fn()} isEditable={true} />
    );
    expect(getAllByRole('button')).toHaveLength(7);
  });

  it('calls onDateClick when entry is clicked', async () => {
    const onDateClick = jest.fn();
    const calendar = createMockCalendar({ entryCount: 1 });
    const { getByRole } = render(
      <CalendarGrid calendar={calendar} onDateClick={onDateClick} isEditable={true} />
    );
    await userEvent.click(getByRole('button'));
    expect(onDateClick).toHaveBeenCalledWith(calendar.entries[0].date);
  });
});
```

Use the equivalent for your stack: Vue Test Utils, Svelte Testing Library,
Angular Testing Library. Run via `{{TEST_CMD}}`.
