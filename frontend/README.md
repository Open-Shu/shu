# Shu Admin Console

A React-based interface for the Shu Backend API.

## Features

- **Dashboard**: Overview of system health, knowledge bases, and sync jobs
- **Knowledge Base Management**: Create, edit, and delete knowledge bases
- **Sync Job Monitoring**: Monitor and manage document synchronization jobs
- **Query Tester**: Test vector similarity and hybrid search queries
- **Health Monitor**: Real-time system health monitoring

## Setup

1. **Install Dependencies**:
   ```bash
   cd frontend
   npm install
   ```

2. **Configure API URL** (optional):
   Create a `.env` file in the frontend directory:
   ```bash
   REACT_APP_API_BASE_URL=http://localhost:8000
   # If not set, the frontend will use same-origin behind your ingress
   ```

3. **Start Development Server**:
   ```bash
   npm start
   ```

The application will be available at `http://localhost:3000`.

## Usage

### Dashboard
- View system overview and quick statistics
- Access quick actions for common tasks

### Knowledge Bases
- Create new knowledge bases with Google Drive configuration
- Edit existing knowledge base settings
- View detailed knowledge base information
- Delete knowledge bases (with confirmation)

### Sync Jobs
- Select a knowledge base to view its sync jobs
- Start new sync operations with custom configuration
- Monitor job progress in real-time
- Cancel running jobs or retry failed jobs
- View detailed job information

### Query Tester
- Select a knowledge base for testing
- Choose between similarity and hybrid search
- Configure query parameters (limit, threshold)
- View results with similarity scores
- Copy result content to clipboard
- Inspect request/response payloads

### Health Monitor
- Real-time system health monitoring
- Database connection status
- API endpoint health checks
- Readiness and liveness probe status
- System metrics overview

## API Integration

The frontend communicates with the Shu API using:
- **Axios** for HTTP requests
- **React Query** for data fetching and caching
- **Material-UI** for the user interface
- **React Router** for navigation

## Development

### Project Structure
```
frontend/
├── public/
│   └── index.html
├── src/
│   ├── components/
│   │   ├── Dashboard.js
│   │   ├── KnowledgeBases.js
│   │   ├── SyncJobs.js
│   │   ├── QueryTester.js
│   │   ├── LLMTester.js
│   │   ├── HealthMonitor.js
│   │   ├── ModernChat.js
│   │   └── ... (other components)
│   ├── layouts/
│   │   ├── AdminLayout.js
│   │   └── UserLayout.js
│   ├── services/
│   │   └── api.js
│   ├── hooks/
│   │   └── useAuth.js
│   ├── App.js
│   └── index.js
├── package.json
└── README.md
```

### Key Dependencies
- **React 18**: UI framework
- **Material-UI**: Component library
- **React Query**: Data fetching and caching
- **React Router**: Navigation
- **Axios**: HTTP client
- **React JSON View**: JSON visualization

### End-to-End Testing (Playwright)
- Install browser binaries once: `npx playwright install`
- Run the Playwright suite (from the `frontend` directory): `npx playwright test`
- Target a single spec while iterating, e.g. `npx playwright test tests/login.spec.ts --headed`

## Configuration

### Environment Variables
- `REACT_APP_API_BASE_URL` (optional): Shu API base URL. If unset, the frontend uses same-origin. Example: http://localhost:8000

### API Endpoints
The frontend uses all major Shu API endpoints:
- Health checks (`/health/*`)
- Knowledge base management (`/knowledge-bases/*`)
- Sync job management (`/sync/*`)
- Query operations (`/query/*`)

## Troubleshooting

### Common Issues

1. **API Connection Errors**:
   - Ensure Shu API is running on the configured URL
   - Check CORS settings in the API
   - Verify network connectivity

2. **Build Errors**:
   - Clear node_modules and reinstall: `rm -rf node_modules && npm install`
   - Check for dependency conflicts

3. **Runtime Errors**:
   - Check browser console for detailed error messages
   - Verify API response format matches expected schema

## Production Build

To create a production build:

```bash
npm run build
```

The build artifacts will be in the `build/` directory.

## Contributing

1. Follow the existing code style and patterns
2. Add proper error handling for API calls
3. Include loading states for async operations
4. Test all CRUD operations thoroughly
5. Update documentation for new features 
