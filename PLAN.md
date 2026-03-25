# Blazor Manager GUI - Comprehensive Implementation Plan

**Project:** EIPS-TT Web Crawler Manager
**Technology:** .NET 8 Blazor Server with SignalR
**Database:** PostgreSQL (crawldb)
**Current Status:** Phase 1-5 Complete, Phase 6 In Progress
**Last Updated:** 2026-03-26

For standalone manager setup/run instructions, see [ManagerApp/README.md](ManagerApp/README.md).

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Technology Stack](#technology-stack)
3. [Architecture Overview](#architecture-overview)
4. [Implementation Phases](#implementation-phases)
5. [Project Structure](#project-structure)
6. [Critical Files & Descriptions](#critical-files--descriptions)
7. [Service Layer Design](#service-layer-design)
8. [UI Components](#ui-components)
9. [Database Integration](#database-integration)
10. [Real-Time Updates](#real-time-updates)
11. [Testing Strategy](#testing-strategy)
12. [Python API Integration](#python-api-integration)
13. [Copilot Optimization Notes](#copilot-optimization-notes)
14. [Future Enhancements](#future-enhancements)

---

## Executive Summary

This plan implements a web-based manager GUI for the EIPS-TT web crawler project. The GUI provides visualization, statistics, and worker management capabilities. The crawler has a complete PostgreSQL database schema but limited worker implementation, so the GUI works with placeholders for missing features while remaining ready for future integration.

**Key Objectives:**
- ✅ Display real crawler statistics from database
- ✅ Visualize page link graph with D3.js
- ✅ Search and filter collected pages
- ✅ Manage crawler workers (with mock data initially)
- ✅ Provide real-time updates via SignalR
- ✅ Implement GitLab-inspired minimalist theme

**Why This Approach:**
- Decouples GUI from crawler implementation
- Allows parallel development of GUI and Python API
- Uses existing database schema without modifications
- Provides foundation for future Python API integration

---

## Technology Stack

### Core Framework
- **.NET 8 SDK** (latest LTS)
- **Blazor Server** (built-in SignalR support)
- **ASP.NET Core 8** (web framework)

### Database & ORM
- **Entity Framework Core 8** (database-first approach)
- **Npgsql.EntityFrameworkCore.PostgreSQL 8.0.10** (PostgreSQL provider)
- **PostgreSQL** (crawldb database)

### Frontend Libraries
- **D3.js v7** (interactive graph visualization)
- **Material Design Icons (MDI)** (via CDN)
- **Custom CSS** (GitLab 18.9 inspired minimalist theme)

### NuGet Packages
```xml
<!-- Core Dependencies -->
Npgsql.EntityFrameworkCore.PostgreSQL 8.0.10
Microsoft.EntityFrameworkCore.Design 8.0.11
Microsoft.EntityFrameworkCore.Tools 8.0.11

<!-- Built-in with Blazor Server -->
Microsoft.AspNetCore.SignalR
Microsoft.AspNetCore.Components
```

### CDN Resources
```html
<!-- Material Design Icons -->
https://cdn.jsdelivr.net/npm/@mdi/font@7.4.47/css/materialdesignicons.min.css

<!-- D3.js -->
https://d3js.org/d3.v7.min.js
```

---

## Architecture Overview

### Layered Architecture

```
┌─────────────────────────────────────────────┐
│        Blazor Components (UI Layer)         │
│  (Pages, Shared Components, Layouts)        │
├─────────────────────────────────────────────┤
│      SignalR Hub (Real-time Layer)          │
│      (CrawlerHub - broadcasts updates)      │
├─────────────────────────────────────────────┤
│      Service Layer (Business Logic)         │
│  (Statistics, Graph, Page, Worker Services) │
├─────────────────────────────────────────────┤
│    Entity Framework Core (Data Access)      │
│      (Auto-generated entity models)         │
├─────────────────────────────────────────────┤
│  PostgreSQL Database (persislent storage)   │
│         (crawldb schema)                    │
└─────────────────────────────────────────────┘
```

### Data Flow

**Statistics Dashboard:**
```
Database (crawldb)
  ↓ (EF Core queries)
StatisticsService
  ↓ (async result)
Dashboard.razor Component
  ↓ (renders HTML)
User's Browser
```

**Graph Visualization:**
```
Database (crawldb.link table)
  ↓ (EF Core queries)
GraphService
  ↓ (transforms to DTO)
GraphWidget.razor Component
  ↓ (calls JS)
D3.js JavaScript Library
  ↓ (renders SVG)
User's Browser (interactive graph)
```

**Real-Time Updates:**
```
Python Crawler API
  ↓ (via HTTP/WebSocket)
SignalR Hub (CrawlerHub)
  ↓ (broadcasts)
All Connected Blazor Clients
  ↓ (receive SignalR events)
Components update UI
  ↓ (StateHasChanged)
User sees live updates
```

---

## Implementation Phases

### Phase 1: Project Setup & Database Integration ✅ COMPLETE
**Goal:** Create Blazor project, scaffold database models, establish connection

**Tasks:**
- ✅ Create new Blazor Server project
- ✅ Add Entity Framework Core NuGet packages
- ✅ Configure PostgreSQL connection string
- ✅ Scaffold EF models from database (database-first)
- ✅ Register DbContext in DI container

**Deliverables:**
- Working Blazor project structure
- 7 EF entity classes auto-generated
- Database connectivity validated

---

### Phase 2: Service Layer Implementation ✅ COMPLETE
**Goal:** Build business logic layer for data access and transformation

**Tasks:**
- ✅ Create IStatisticsService & StatisticsService
- ✅ Create IGraphService & GraphService
- ✅ Create IPageService & PageService
- ✅ Create IWorkerService & WorkerService (mock)
- ✅ Register all services in Program.cs

**Deliverables:**
- 4 service interfaces with clear contracts
- 4 service implementations with async database queries
- Mock worker data for development
- Parallel query execution for performance

**Key Service Methods:**

| Service | Method | Purpose |
|---------|--------|---------|
| Statistics | GetStatisticsAsync() | Get all dashboard metrics |
| | GetPageTypeCountsAsync() | Count by page type |
| | GetDuplicateCountAsync() | Count duplicates |
| Graph | GetGraphDataAsync(limit) | Get nodes + edges for D3 |
| | GetIncomingLinkCountsAsync() | Size nodes by popularity |
| Page | SearchPagesAsync(term, type) | Full-text search pages |
| | GetPageDetailsAsync(id) | Get complete page with relations |
| Worker | GetAllWorkersAsync() | List all workers (mock) |
| | StartWorkerAsync(id) | Start worker (future API call) |

---

### Phase 3: UI Theme & Layout ✅ COMPLETE
**Goal:** Implement GitLab-inspired theme and main layout structure

**Tasks:**
- ✅ Create theme.css with CSS custom properties
- ✅ Create app.css with global styles
- ✅ Build MainLayout.razor with sidebar navigation
- ✅ Update NavMenu.razor with MDI icons
- ✅ Create Dashboard.razor home page
- ✅ Update App.razor with CSS/JS includes

**Deliverables:**
- Consistent color palette with dark mode support
- Responsive grid system
- Sidebar navigation with icon support
- Dashboard with statistics cards
- MDI icon integration

**Theme Colors:**
- Primary: `#1f75cb` (blue)
- Success: `#217645` (green)
- Error: `#c91c00` (red)
- Warning: `#c17d10` (orange)
- Sidebar: `#303030` (dark)

---

### Phase 4: SignalR Infrastructure ✅ COMPLETE
**Goal:** Set up real-time communication hub

**Tasks:**
- ✅ Create CrawlerHub.cs SignalR hub
- ✅ Map hub in Program.cs
- ✅ Define hub methods for broadcasts

**Deliverables:**
- SignalR hub at `/crawlerhub` endpoint
- Methods for status, page, and statistics broadcasts
- Connection/disconnection logging
- Ready for Python API integration

**Hub Methods:**
```csharp
BroadcastWorkerStatus(WorkerViewModel)
BroadcastPageCrawled(PageSearchDto)
BroadcastStatisticsUpdate(StatisticsViewModel)
```

---

### Phase 5: Visualization Components ✅ COMPLETE
**Goal:** Implement D3.js graph and complete remaining pages

**Tasks:**
- ✅ Create ForceGraph.razor component
- ✅ Create graph.js rendering logic
- ✅ Create Workers.razor page
- ✅ Create Graph.razor page
- ✅ Create PagesResults.razor page
- ⏳ End-to-end test pass and editor diagnostics cleanup

**Implemented in current codebase:**
- Force-directed graph with search focus and SVG export
- Worker management page with status filters and action controls
- Page results/search view integrated with page service
- Shared UI components: StatCard, IconButton, WorkerStatusPieChart, PlaceholderNotice, ForceGraph

---

### Phase 6: Testing & Validation (Future)
**Goal:** Verify all features work with real data

**Testing Scope:**
- Database connection tests
- Service query accuracy
- Component rendering
- Graph interactions
- Search functionality

---

### Phase 7: Python API Integration (Future)
**Goal:** Replace mock data with real crawler API

**Tasks (when Python API ready):**
- Build Flask REST endpoints
- Implement worker lifecycle management
- Setup WebSocket for real-time updates
- Replace mock WorkerService with API-based implementation

---

## Project Structure

```
/home/tibor/Repos/EIPS-TT/
├── ManagerApp/                          # Main Blazor Server project
│   ├── ManagerApp.csproj                # Project file with dependencies
│   ├── Program.cs                       # Application entry, DI setup, hub mapping
│   ├── appsettings.json                 # DB connection, logging config
│   │
│   ├── Data/                            # EF Core Models (auto-scaffolded)
│   │   ├── CrawldbContext.cs           # DbContext
│   │   ├── Site.cs                     # Site entity
│   │   ├── Page.cs                     # Page entity with navigation
│   │   ├── PageType.cs                 # PageType lookup
│   │   ├── PageDatum.cs                # Binary data storage
│   │   ├── DataType.cs                 # DataType lookup
│   │   └── Image.cs                    # Image storage
│   │
│   ├── Models/                          # View Models & DTOs
│   │   └── ViewModels.cs               # StatisticsViewModel, GraphNodeDto, etc.
│   │
│   ├── Services/                        # Business Logic Layer
│   │   ├── IStatisticsService.cs       # Statistics interface
│   │   ├── StatisticsService.cs        # Statistics implementation
│   │   ├── IGraphService.cs            # Graph interface
│   │   ├── GraphService.cs             # Graph implementation
│   │   ├── IPageService.cs             # Page search interface
│   │   ├── PageService.cs              # Page search implementation
│   │   ├── IWorkerService.cs           # Worker interface
│   │   └── WorkerService.cs            # Worker mock implementation
│   │
│   ├── Hubs/                            # SignalR Hubs
│   │   └── CrawlerHub.cs               # Real-time broadcast hub
│   │
│   ├── Components/                      # Blazor Components
│   │   ├── App.razor                   # Root component (updated with CSS/JS)
│   │   ├── Routes.razor                # Routing component
│   │   ├── Layout/
│   │   │   ├── MainLayout.razor        # App shell with sidebar
│   │   │   ├── MainLayout.razor.css    # Layout-specific styles
│   │   │   ├── NavMenu.razor           # Sidebar navigation (MDI icons)
│   │   │   ├── NavMenu.razor.css       # Nav styles
│   │   │   └── ReconnectModal.razor    # Reconnection UI
│   │   │
│   │   ├── Pages/                      # Page Components (routed)
│   │   │   ├── Home.razor              # Dashboard page (/)
│   │   │   ├── Workers.razor           # Worker management (/workers)
│   │   │   ├── GraphVisualization.razor # Full graph (/graph)
│   │   │   ├── PageResults.razor       # Page search (/pages)
│   │   │   └── Error.razor             # Error page
│   │   │
│   │   └── Shared/                     # Reusable Components
│   │       ├── StatCard.razor          # Statistics card component
│   │       ├── IconButton.razor        # MDI icon button
│   │       ├── GraphWidget.razor       # Embeddable graph component
│   │       ├── WorkerStatusPieChart.razor # Worker status chart
│   │       ├── WorkerListItem.razor    # Worker list row
│   │       ├── PageListItem.razor      # Page search row
│   │       └── SearchBar.razor         # Search input component
│   │
│   ├── wwwroot/                        # Static Files & Assets
│   │   ├── css/
│   │   │   ├── theme.css               # GitLab-inspired theme variables
│   │   │   ├── app.css                 # Global styles & components
│   │   │   └── components.css          # Component-specific styles
│   │   ├── js/
│   │   │   ├── d3-graph.js             # D3.js graph rendering
│   │   │   └── interop.js              # Blazor-JS interop helpers
│   │   ├── favicon.png
│   │   └── lib/
│   │
│   ├── Properties/
│   │   └── launchSettings.json         # Development server settings
│   │
│   └── obj/, bin/                      # Build artifacts (auto-generated)
│
├── README.md                            # Project documentation
└── Plan.md                              # This file
```

---

## Critical Files & Descriptions

### Program.cs
**Purpose:** Application entry point, dependency injection container, middleware pipeline

**Key Configuration:**
- Registers DbContext with PostgreSQL connection string
- Registers service interfaces with implementations (scoped lifetime)
- Configures SignalR for real-time updates
- Maps SignalR hub at `/crawlerhub` endpoint

**Connection String:**
```
Host=localhost;Port=5432;Database=crawldb;Username=postgres;Password=postgres
```

---

### Data/CrawldbContext.cs
**Purpose:** EF Core DbContext for database access

**DbSets:**
- `DbSet<Site>` - Website domains
- `DbSet<Page>` - Crawled pages
- `DbSet<Image>` - Page images
- `DbSet<PageDatum>` - Binary documents
- `DbSet<PageType>` - Page type lookup
- `DbSet<DataType>` - Data type lookup

**Generated from database schema (do not modify manually)**

---

### Services/StatisticsService.cs
**Purpose:** Query database for crawler statistics

**Key Methods:**
- `GetStatisticsAsync()` - Comprehensive stats (parallel queries)
- `GetDuplicateCountAsync()` - Count duplicate pages
- `GetImageCountAsync()` - Count all images
- `GetAverageImagesPerPageAsync()` - Calculate average

**Performance:** Uses `Task.WhenAll()` for parallel execution

---

### Services/GraphService.cs
**Purpose:** Transform link table data into D3.js format

**Key Methods:**
- `GetGraphDataAsync(limit)` - Get nodes + edges
- `GetIncomingLinkCountsAsync()` - Calculate node sizes

**Output Format:**
```csharp
GraphDataDto {
  Nodes: List<GraphNodeDto>,  // Pages with size
  Links: List<GraphLinkDto>   // Page relationships
}
```

---

### Components/Pages/Home.razor
**Purpose:** Dashboard home page with statistics

**Features:**
- Statistics cards (Sites, Pages, Duplicates, Images)
- Binary file breakdown
- Worker status summary
- Quick action buttons
- Real-time data from database
- Error handling

**Data Source:** StatisticsService via dependency injection

---

### Hubs/CrawlerHub.cs
**Purpose:** SignalR hub for real-time updates

**Broadcast Methods:**
- `BroadcastWorkerStatus()` - Worker status changes
- `BroadcastPageCrawled()` - New pages crawled
- `BroadcastStatisticsUpdate()` - Statistics updates

**Usage:** Called by Python crawler API to push updates to all connected clients

---

### wwwroot/css/theme.css
**Purpose:** Theme color system and CSS custom properties

**CSS Variables:**
- Colors (primary, neutral, status)
- Spacing values
- Border radius
- Shadows
- Transitions
- Font definitions

**Dark Mode Support:** `@media (prefers-color-scheme: dark)` block

---

### wwwroot/css/app.css
**Purpose:** Global application styles

**Includes:**
- Layout styles (flexbox, grid)
- Component styles (cards, buttons, badges)
- Typography
- Form elements
- Utility classes
- Responsive breakpoints

---

## Service Layer Design

### Service Interfaces

#### IStatisticsService
```csharp
Task<StatisticsViewModel> GetStatisticsAsync()
Task<Dictionary<string, int>> GetPageTypeCountsAsync()
Task<int> GetDuplicateCountAsync()
Task<int> GetImageCountAsync()
Task<Dictionary<string, int>> GetBinaryFileCountsAsync()
Task<double> GetAverageImagesPerPageAsync()
```

#### IGraphService
```csharp
Task<GraphDataDto> GetGraphDataAsync(int? limit = null)
Task<GraphDataDto> GetGraphDataForPageAsync(int pageId, int depth = 2)
Task<Dictionary<int, int>> GetIncomingLinkCountsAsync()
```

#### IPageService
```csharp
Task<List<PageSearchDto>> SearchPagesAsync(
    string? searchTerm,
    string? pageType,
    int skip = 0,
    int take = 50)
Task<int> GetSearchResultsCountAsync(string? searchTerm, string? pageType)
Task<Page?> GetPageDetailsAsync(int pageId)
```

#### IWorkerService
```csharp
Task<List<WorkerViewModel>> GetAllWorkersAsync()
Task<WorkerViewModel?> GetWorkerAsync(int id)
Task<bool> StartWorkerAsync(int id)
Task<bool> StopWorkerAsync(int id)
Task<bool> PauseWorkerAsync(int id)
Task<Dictionary<string, int>> GetWorkerStatusCountsAsync()
```

---

### Dependency Injection Registration

```csharp
// In Program.cs
builder.Services.AddDbContext<CrawldbContext>(options =>
    options.UseNpgsql(connectionString));

builder.Services.AddScoped<IStatisticsService, StatisticsService>();
builder.Services.AddScoped<IGraphService, GraphService>();
builder.Services.AddScoped<IPageService, PageService>();
builder.Services.AddScoped<IWorkerService, WorkerService>();
```

**Lifetime:**
- **Scoped:** New instance per HTTP request
- Allows efficient database connection pooling
- Safe for multi-user scenarios

---

## UI Components

### Layout Components

#### MainLayout.razor
- App container with flexbox
- Sidebar navigation
- Main content area
- Routes placeholder

#### NavMenu.razor
- Sidebar navigation menu
- NavLink components with MDI icons
- Active route highlighting
- 4 main routes: Dashboard, Workers, Graph, Pages

---

### Page Components

#### Home.razor (Dashboard)
**Route:** `/`

**Features:**
- Statistics cards grid
- Binary files summary
- Worker status badges
- Quick action buttons
- Real-time last updated timestamp

**Services Used:**
- IStatisticsService
- IWorkerService

#### Workers.razor (Future)
**Route:** `/workers`

**Planned Features:**
- Worker list with status
- Start/stop/pause controls
- Worker metrics
- Configuration panel

#### GraphVisualization.razor (Future)
**Route:** `/graph`

**Planned Features:**
- Full-screen D3 graph
- Zoom/pan controls
- Node click details
- Filter options

#### PageResults.razor (Future)
**Route:** `/pages`

**Planned Features:**
- Search bar
- Results list with pagination
- Filter by type
- Page details on click

---

### Shared Components

#### StatCard.razor
Reusable statistic display card

#### GraphWidget.razor
Embeddable D3.js graph component

#### WorkerStatusPieChart.razor
Visual worker status breakdown

#### SearchBar.razor
Reusable search input

---

## Database Integration

### Entity Relationships

```
Site (1) ──> (*) Page
             ├─> Images
             ├─> PageData
             └─> Links (many-to-many to other Pages)

Page ──> PageType (lookup)
PageData ──> DataType (lookup)
```

### Query Patterns

**Statistics Query:**
```csharp
// Parallel queries for performance
var sites = await _context.Sites.CountAsync();
var pages = await _context.Pages.CountAsync();
var duplicate = await _context.Pages
    .CountAsync(p => p.PageTypeCode == "DUPLICATE");
```

**Graph Query:**
```csharp
// Get pages with incoming link counts
var nodes = await _context.Pages
    .Where(p => p.PageTypeCode == "HTML")
    .Include(p => p.Site)
    .ToListAsync();

// Get relationships
var links = await nodeIds.SelectMany(...)
    .ToListAsync();
```

**Search Query:**
```csharp
// Full-text search on URL and content
var results = await _context.Pages
    .Where(p => searchTerm == null ||
        p.Url.ToLower().Contains(searchTerm))
    .OrderByDescending(p => p.AccessedTime)
    .Skip(skip)
    .Take(take)
    .ToListAsync();
```

### Performance Optimization

- **Parallel Execution:** Use `Task.WhenAll()` for independent queries
- **Query Projection:** Select only needed fields with `.Select()`
- **Indexing:** Database has indexes on frequently queried columns
- **Pagination:** Use `.Skip().Take()` for large result sets
- **Eager Loading:** Use `.Include()` to avoid N+1 queries

---

## Real-Time Updates

### SignalR Integration

**Hub Registration:**
```csharp
// In Program.cs
app.MapHub<CrawlerHub>("/crawlerhub");
```

**Client Connection (in Blazor component):**
```csharp
hubConnection = new HubConnectionBuilder()
    .WithUrl(Navigation.ToAbsoluteUri("/crawlerhub"))
    .Build();

hubConnection.On<WorkerViewModel>("WorkerStatusUpdated", worker => {
    // Handle update
});

await hubConnection.StartAsync();
```

**Broadcast from Python API:**
```python
hubConnection.send('BroadcastWorkerStatus', worker_data)
```

---

## Testing Strategy

### Database Testing
1. Verify connection string
2. Check EF models mapped correctly
3. Run sample queries

### Service Testing
1. Mock database for unit tests
2. Verify query results
3. Check error handling

### Component Testing
1. Test rendering with sample data
2. Verify navigation works
3. Check form inputs

### Integration Testing
1. Full application flow
2. Database to UI
3. SignalR updates

---

## Python API Integration

### Required Flask Endpoints (Future)

```python
POST   /api/workers/start              # Spawn worker
POST   /api/workers/{id}/stop          # Stop worker
POST   /api/workers/{id}/pause         # Pause worker
GET    /api/workers                    # List workers
GET    /api/workers/{id}/status        # Worker details
PUT    /api/workers/{id}/config        # Update config
POST   /api/frontier/seed              # Add URLs
GET    /api/statistics                 # Get stats
```

### Required Implementations

1. **Multi-Worker System**
   - Worker class with crawl loop
   - Thread pool management
   - Worker state tracking

2. **Frontier Management**
   - Priority queue for URLs
   - Deduplication before adding
   - Search strategy implementation

3. **HTML Parsing**
   - Link extraction (href + onclick)
   - Image extraction
   - Relative URL resolution

4. **API Communication**
   - Push updates to SignalR hub
   - Expose REST endpoints
   - Handle authentication

---

## Copilot Optimization Notes

### Writing Prompts for Copilot

**Well-formed prompts for code generation:**

1. **For Blazor Components:**
   > "Create a Blazor component that displays worker status with Material Design Icons. Show worker name, status badge (using CSS classes), current page count, and action buttons (start/stop/pause). Use dependency injection for IWorkerService."

2. **For Services:**
   > "Implement a C# service method that queries the database to get page statistics: total pages, HTML pages, duplicates. Use async/await and parallel queries with Task.WhenAll() for performance."

3. **For D3.js:**
   > "Create a JavaScript function that renders a force-directed graph using D3.js v7. Nodes are pages sized by incoming links. Links show page relationships. Add zoom, pan, and drag interactions. Use the provided data format with nodes[] and links[] arrays."

### Code Organization for Clarity

**What Makes Code Copilot-Friendly:**
- ✅ Clear separation of concerns
- ✅ Consistent naming conventions
- ✅ Interfaces before implementations
- ✅ XML doc comments on public methods
- ✅ TODO comments for future work
- ✅ Type safety (avoid `dynamic`)
- ✅ Async/await patterns
- ✅ Dependency injection

**Patterns Used:**
- Service interfaces standardize contracts
- Entity DTOs for API communication
- Repository pattern via Entity Framework
- Dependency injection in Program.cs

---

## Future Enhancements

### Phase 8: Advanced Features
- [ ] Real-time worker position tracking
- [ ] Crawler performance analytics
- [ ] Export crawler data to CSV/JSON
- [ ] Scheduled crawl jobs
- [ ] Proxy rotation management
- [ ] Custom crawl profiles

### Phase 9: Worker UI Enhancement
- [ ] Worker performance metrics
- [ ] Error rate tracking
- [ ] Spider trap detection display
- [ ] Frontier visualization
- [ ] Crawl queue status

### Phase 10: Graph Enhancements
- [ ] Filter by domain
- [ ] Search within graph
- [ ] Show page content on hover
- [ ] Path highlighting
- [ ] Subgraph extraction
- [ ] Export graph visualization

### Phase 11: Analytics Dashboard
- [ ] Crawl speed metrics
- [ ] Success/error rates
- [ ] Content type distribution
- [ ] Spider trap statistics
- [ ] Duplicate rate tracking

### Phase 12: Administration
- [ ] User authentication
- [ ] Audit logging
- [ ] Role-based access
- [ ] Configuration management
- [ ] API key management

---

## Known Limitations

### Current (MVP)
- Worker data is mock (no real crawler connection)
- Link table may be empty (awaiting crawler implementation)
- No persistence for UI state
- No user authentication
- All users see same data

### Database-Level
- HTML content not indexed (for full-text search)
- No materialized views for complex queries
- Limited query optimization

---

## Deployment Instructions

### Development

```bash
# Navigate to project
cd /home/tibor/Repos/EIPS-TT/ManagerApp

# Run application
dotnet run --launch-profile https

# Access at https://localhost:7282
```

### Production (Future)
- Docker containerization
- HTTPS certificate setup
- Database remote connection
- Reverse proxy configuration
- CI/CD deployment

---

## Support & Resources

- **Blazor Documentation:** https://learn.microsoft.com/blazor
- **Entity Framework Core:** https://learn.microsoft.com/ef/core
- **D3.js Documentation:** https://d3js.org
- **SignalR Documentation:** https://learn.microsoft.com/signalr
- **Material Design Icons:** https://materialdesignicons.com

---

## Version History

| Version | Date | Status | Notes |
|---------|------|--------|-------|
| 1.0 | 2026-03-26 | In Progress | Initial implementation with Phases 1-4 complete |
| 1.1 | TBD | Pending | Phase 5 - Graph visualization |
| 1.2 | TBD | Pending | Phase 6 - Testing & validation |
| 2.0 | TBD | Pending | Phase 7 - Python API integration |

---

**Document Last Updated:** 2026-03-26
**Status:** In Active Development
**Next Phase:** Graph Visualization (Phase 5)

