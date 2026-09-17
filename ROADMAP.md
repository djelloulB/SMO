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
- [ ] Implement LinkedIn connector with OAuth
- [ ] Implement LinkedIn post publishing
- [ ] Add content ingestion from RSS
- [ ] Add CLI commands for full workflow
- [ ] Write tests for connector
- [ ] Security validation
- [ ] Documentation updates

---

## Phase 2: Expanding Functionality (Target: 3-6 months)

### 1. WordPress Connector
- [ ] WordPress REST API integration
- [ ] Application Password authentication
- [ ] Post creation and publishing
- [ ] Draft management
- [ ] Image upload support

### 2. X (Twitter) Connector
- [ ] API v2 integration
- [ ] Twitter OAuth flow
- [ ] Tweet posting
- [ ] Thread support
- [ ] Quote tweets
- [ ] Rate limiting handling

### 3. Image Generation
- [ ] DALL-E integration
- [ ] Prompt-based image generation
- [ ] Image variation
- [ ] Multiple image providers (Stability, Midjourney placeholder)
- [ ] Image resizing and optimization

### 4. MCP Server
- [ ] MCP stdio server
- [ ] MCP HTTP server
- [ ] Tool implementation (read, content, control, admin)
- [ ] Resource implementation
- [ ] Agent key management

### 5. Synology Deployment
- [ ] Container Manager deployment guide
- [ ] Reverse proxy configuration
- [ ] Tailscale deployment guide
- [ ] Scheduled tasks integration

### 6. Advanced Scheduling
- [ ] APScheduler integration
- [ ] Multiple scheduling strategies
- [ ] Window-based scheduling
- [ ] Retry logic with exponential backoff
- [ ] Circuit breaker pattern

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
- [ ] Metrics collection
- [ ] Engagement analysis
- [ ] Time window optimization
- [ ] Multi-platform scheduling

### 5. Admin MCP Tools
- [ ] Configuration management
- [ ] Token rotation
- [ ] Health monitoring
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
| Phase 1: MVP | 🚧 In Progress | 60% | Current |
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
