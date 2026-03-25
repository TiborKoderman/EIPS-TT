# Implementation Todo List

For standalone manager setup/run instructions, see [ManagerApp/README.md](ManagerApp/README.md).

## Status Overview

**Completed:** 11/12 tasks
**In Progress:** 0/12 tasks
**Pending:** 1/12 tasks

---

## Tasks

### ✅ Phase 1: Project Setup & Database Integration
- [x] Initialize .NET Blazor Server project and add required NuGet packages
- [x] Configure database connection in appsettings.json
- [x] Scaffold EF Core models from PostgreSQL database (database-first)

### ✅ Phase 2: Service Layer
- [x] Create view models and DTOs
- [x] Create service layer (Statistics, Graph, Page, Worker services)
- [x] Register services in Program.cs (DbContext, services, SignalR)

### ✅ Phase 3: UI Theme & Layout
- [x] Create theme CSS (GitLab-inspired) and global app styles
- [x] Create main layout and navigation menu
- [x] Create Dashboard page with statistics

### ✅ Phase 4: SignalR Infrastructure
- [x] Create SignalR hub for real-time updates

### ⏳ Phase 5: Visualization
- [x] Create D3.js graph visualization component
- [ ] Test the application (database connection, statistics, graph)

---

## Completed Tasks Details

### ✅ Initialize .NET Blazor Server project and add required NuGet packages
**Status:** COMPLETED
**Description:** Created new Blazor Server project with .NET 8 and added Entity Framework Core, PostgreSQL provider, and tools

**Files Created/Modified:**
- `/home/tibor/Repos/EIPS-TT/ManagerApp/` - Project directory
- Added NuGet packages:
  - Npgsql.EntityFrameworkCore.PostgreSQL 8.0.10
  - Microsoft.EntityFrameworkCore.Design 8.0.11
  - Microsoft.EntityFrameworkCore.Tools 8.0.11

---

### ✅ Configure database connection in appsettings.json
**Status:** COMPLETED
**Description:** Added PostgreSQL connection string to appsettings.json

**Files Modified:**
- `appsettings.json` - Added `ConnectionStrings:CrawldbConnection`

---

### ✅ Scaffold EF Core models from PostgreSQL database (database-first)
**Status:** COMPLETED
**Description:** Used dotnet-ef to scaffold entity models from existing crawldb database schema

**Files Created:**
- `Data/CrawldbContext.cs` - DbContext
- `Data/Site.cs` - Site entity
- `Data/Page.cs` - Page entity with navigation properties
- `Data/PageType.cs` - PageType lookup entity
- `Data/PageDatum.cs` - Binary data storage entity
- `Data/DataType.cs` - DataType lookup entity
- `Data/Image.cs` - Image storage entity

---

### ✅ Create view models and DTOs
**Status:** COMPLETED
**Description:** Created all view models and data transfer objects for UI communication

**Files Created:**
- `Models/ViewModels.cs` containing:
  - `StatisticsViewModel` - Dashboard statistics
  - `GraphNodeDto` - D3.js node representation
  - `GraphLinkDto` - D3.js edge representation
  - `GraphDataDto` - Complete graph data container
  - `PageSearchDto` - Search result representation
  - `WorkerViewModel` - Worker status data

---

### ✅ Create service layer (Statistics, Graph, Page, Worker services)
**Status:** COMPLETED
**Description:** Implemented all business logic services with database queries

**Files Created:**
- `Services/StatisticsService.cs` - Query database for statistics
- `Services/GraphService.cs` - Transform link data for D3.js
- `Services/PageService.cs` - Page search and filtering
- `Services/WorkerService.cs` - Mock worker management (connects to Python API later)

**Key Features:**
- Parallel async queries for performance
- Efficient LINQ transformations
- Mock data for workers pending Python API implementation

---

### ✅ Register services in Program.cs (DbContext, services, SignalR)
**Status:** COMPLETED
**Description:** Configured dependency injection container with all services

**File Modified:**
- `Program.cs` - Added:
  - DbContext registration
  - Service registrations (scoped)
  - SignalR configuration
  - Hub mapping

---

### ✅ Create theme CSS (GitLab-inspired) and global app styles
**Status:** COMPLETED
**Description:** Implemented minimalist theme inspired by GitLab 18.9 light theme

**Files Created:**
- `wwwroot/css/theme.css` - CSS custom properties and dark mode support
- `wwwroot/css/app.css` - Global styles, components, utility classes

**Theme Features:**
- Primary blue: `#1f75cb`
- Neutral color palette
- Status-based colors (success, error, warning, info)
- Responsive grid system
- Component-specific styles
- Smooth transitions and hover states
- Dark mode support (CSS variables ready)

---

### ✅ Create main layout and navigation menu
**Status:** COMPLETED
**Description:** Built app shell with sidebar navigation and responsive layout

**Files Modified:**
- `Components/Layout/NavMenu.razor` - Navigation with MDI icons
- `Components/Layout/MainLayout.razor` - Main layout structure

**Navigation Items:**
- Dashboard (mdi-view-dashboard)
- Workers (mdi-robot)
- Node Graph (mdi-graph)
- Collected Pages (mdi-file-document-multiple)

---

### ✅ Create Dashboard page with statistics
**Status:** COMPLETED
**Description:** Implemented Dashboard home page with real database statistics

**File Modified:**
- `Components/Pages/Home.razor` → Dashboard page

**Dashboard Features:**
- Statistics cards (Sites, Pages, Duplicates, Images)
- Binary file breakdown
- Worker status summary
- Quick action buttons
- Real-time data from database
- Error handling and loading states

---

### ✅ Create SignalR hub for real-time updates
**Status:** COMPLETED
**Description:** Implemented SignalR hub for broadcasting crawler updates

**File Created:**
- `Hubs/CrawlerHub.cs`

**Hub Methods:**
- `BroadcastWorkerStatus()` - Push worker status updates
- `BroadcastPageCrawled()` - Announce new pages crawled
- `BroadcastStatisticsUpdate()` - Broadcast statistics changes
- Connection/disconnection logging

---

## Pending Tasks

### ✅ Create D3.js graph visualization component
**Status:** COMPLETED
**Description:** Interactive graph visualization is implemented.

**Implemented Files:**
- `Components/Shared/ForceGraph.razor`
- `Components/Pages/Graph.razor`
- `wwwroot/js/graph.js`

**Implemented Features:**
- Force-directed layout
- Zoom, pan, drag interactions
- Search-based focus
- SVG export

---

### ⏳ Test the application (database connection, statistics, graph)
**Status:** PENDING
**Priority:** High
**Description:** Verify all components work correctly with real database

**Testing Checklist:**
- [ ] Application starts without errors
- [ ] Database connection established
- [ ] Dashboard loads statistics from database
- [ ] Statistics calculations are correct
- [ ] Navigation works between pages
- [ ] MDI icons display correctly
- [ ] Theme applies correctly
- [ ] Graph component renders and interacts
- [ ] SignalR hub initializes
- [ ] Worker mock data displays

**Known Issues:**
- None currently

---

## Next Steps

1. **Test with Database:**
   - Verify statistics are accurate
   - Check graph with real data
   - Test search functionality
   - Validate worker actions/mocks and page details flow

2. **Python API Integration (Future):**
   - Build Flask/FastAPI REST endpoints
   - Replace mock data with real API calls
   - Implement WebSocket/SignalR real-time updates

3. **Editor Diagnostics Cleanup:**
   - Resolve residual Razor language-server component-resolution diagnostics if they persist
   - Keep `dotnet build` as source-of-truth for compile validation

---

## Build Status

**Last Build:** ✅ SUCCEEDED (0 errors, 0 warnings)
**Build Time:** ~2.68 seconds
**Application Status:** ✅ RUNNING

**Application URLs:**
- HTTP: http://localhost:5150
- HTTPS: https://localhost:7282

---

## Project Statistics

| Metric | Value |
|--------|-------|
| Services | 4 (Statistics, Graph, Page, Worker) |
| View Models/DTOs | 6 |
| Razor Components | 3+ (Layout, Pages, Shared) |
| CSS Files | 2 (theme.css, app.css) |
| Database Entities | 7 (Site, Page, Image, PageDatum, DataType, PageType, and Link via relationship) |
| SignalR Hubs | 1 (CrawlerHub) |

---

## Technology Stack

- **.NET 8** Blazor Server
- **Entity Framework Core 8** (Database-first)
- **PostgreSQL** via Npgsql
- **D3.js v7** (Graph visualization)
- **Material Design Icons** (Icon library)
- **SignalR** (Real-time updates)

---

## Timeline

- **Phase 1 (Setup):** ✅ COMPLETE
- **Phase 2 (Services):** ✅ COMPLETE
- **Phase 3 (UI):** ✅ COMPLETE
- **Phase 4 (SignalR):** ✅ COMPLETE
- **Phase 5 (Visualization):** ⏳ IN PROGRESS
- **Phase 6 (Testing):** ⏳ PENDING
- **Phase 7 (Python Integration):** 🔮 FUTURE

