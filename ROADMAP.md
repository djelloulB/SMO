# Roadmap

## Current Status (v0.1.0 - Phase 1 MVP)

**Goal:** Build a text-only LinkedIn orchestrator with strong security foundation.

**Features Implemented:**
- ✅ Immutable security layer (REC-002)
- ✅ OAuth CSRF protection (REC-004)
- ✅ SSRF guard (REC-005)
- ✅ Path confinement (REC-006)
- ✅ Secret redaction (REC-007)
- ✅ Append-only audit log (REC-008)
- ✅ Secret isolation (REC-001)
- ✅ Build hygiene (REC-003)

**TODO for Phase 1:**
- [x] Implement LinkedIn connector with OAuth
- [x] Implement LinkedIn post publishing (member profile + organization page)
- [x] Add content ingestion from RSS (RSS/Atom + local files)
- [x] Add CLI commands for full workflow (ingest → drafts → show → approve/reject → publish)
- [x] Write tests for connector, content pipeline and publishing (99-test pytest suite)
- [x] Security validation (REC-001/002/004/005/006/007/008)
- [x] Documentation updates (README, .env.example, CHANGELOG)

---

## Phase 2: Expanding Functionality (Target: 3-6 months)

### 1. WordPress Connector
- [x] WordPress REST API integration
- [x] Application Password authentication
- [x] Post creation and publishing
- [ ] Draft management (the connector accepts a status, not yet exposed to the workflow)
- [x] Image upload support (WordPress media library, LinkedIn `/rest/images`)

### 2. X (Twitter) Connector
- [x] API v2 integration
- [x] Twitter OAuth flow (OAuth 2.0 PKCE)
- [x] Tweet posting
- [ ] Thread support
- [ ] Quote tweets
- [ ] Rate limiting handling (429 is retried with backoff; no quota tracking yet)

### 3. Image Generation
- [x] Image generation via OpenAI GPT Image (DALL-E models are retired)
- [x] Prompt-based image generation
- [x] Attach an agent-supplied image and publish it with the post (WordPress, LinkedIn)
- [ ] Image variation
- [ ] Multiple image providers (Stability, Midjourney placeholder)
- [ ] Image resizing and optimization

### 4. MCP Server
- [x] MCP stdio server
- [x] MCP HTTP server
- [x] Tool implementation (read, content, control, admin) — partial, see docs/MCP.md section 6
- [ ] Resource implementation
- [ ] Agent key management

### 5. Synology Deployment
- [x] Container Manager deployment guide (docs/SYNOLOGY.md, not yet tested on a NAS)
- [x] Reverse proxy configuration (scenario A, documented)
- [x] Tailscale deployment guide (scenario B, documented)
- [ ] Scheduled tasks integration (scheduler runs inside the container instead)

### 6. Advanced Scheduling
- [x] APScheduler integration
- [x] Multiple scheduling strategies
- [x] Window-based scheduling
- [x] Retry logic with exponential backoff
- [x] Circuit breaker pattern

---

## Phase 3: Video & Multi-Platform (Target: 6-12 months)

### 1. TikTok Connector
- [ ] TikTok Content Posting API
- [ ] Draft workflow
- [ ] Video upload
- [ ] Hashtag management

### 2. Instagram/Facebook Connectors
- [ ] Instagram Reels support
- [ ] Facebook Page API integration
- [ ] Multi-image carousel support
- [ ] Story posting (if API available)

### 3. YouTube Shorts Connector
- [ ] YouTube Data API v3
- [ ] Video upload
- [ ] Metadata management
- [ ] Quota awareness

### 4. Self-Optimizing Schedule
- [x] Metrics collection (likes only for LinkedIn)
- [x] Engagement analysis (newest snapshot per post, 30-day half-life, per platform)
- [x] Time window optimization (exploit best hour, explore another daytime hour)
- [x] Multi-platform scheduling

### 5. Admin MCP Tools
- [x] Configuration management (mutable keys only)
- [x] Token rotation (LinkedIn access token refresh)
- [x] Health monitoring (`get_health` with platform states)
- [ ] Batch operations

### 6. Multi-Account Support
- [ ] Multiple accounts per platform
- [ ] Account switching
- [ ] Account health monitoring
- [ ] Token refresh automation

---

## Future Directions

### Phase 4: Advanced Features (Target: 12+ months)

### 1. Video Editing
- [ ] Basic video trimming
- [ ] Overlay text
- [ ] Transitions
- [ ] Music integration

### 2. Advanced Analytics
- [ ] Deep metrics analysis
- [ ] Competitor analysis
- [ ] Audience insights
- [ ] Export reports

### 3. AI-Powered Content
- [ ] GPT-4 integration for better content
- [ ] Image-to-text analysis
- [ ] Hashtag generation
- [ ] Copywriting assistance

### 4. Integration Ecosystem
- [ ] Zapier integration
- [ ] Webhooks support
- [ ] REST API endpoints
- [ ] GraphQL API

### 5. Advanced Security
- [ ] Two-factor authentication
- [ ] Hardware security keys
- [ ] Encrypted key storage
- [ ] Audit trail dashboard

---

## Technical Priorities

### Short Term (Next 1-3 months)
1. Complete Phase 1 MVP
2. Write comprehensive tests
3. Documentation improvements
4. Performance optimization

### Medium Term (3-6 months)
1. WordPress connector
2. X connector
3. Image generation
4. MCP server

### Long Term (6-12 months)
1. Video connectors
2. Self-optimizing schedule
3. Multi-account support
4. Advanced analytics

---

## Roadmap Status

| Phase | Status | Progress | Timeline |
|---|---|---|---|
| Phase 1: MVP | 🚧 In Progress | 85% | Current |
| Phase 2: Expansion | 📅 Planned | 0% | 3-6 months |
| Phase 3: Video | 📅 Planned | 0% | 6-12 months |
| Phase 4: Advanced | 🔮 Future | 0% | 12+ months |

---

## Contributing to the Roadmap

We welcome contributions to this roadmap! If you have ideas for features or improvements:

1. Open an issue on GitHub
2. Discuss your idea with the maintainers
3. Create a pull request with your proposed changes

The roadmap is a living document and will be updated as we progress.

---

**Stay tuned for updates as we build SMO!** 🚀
