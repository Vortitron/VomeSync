# VomeSync WebServer Dockerfile
FROM node:18-alpine

# Create app directory
WORKDIR /usr/src/app

# Add non-root user for security
RUN addgroup -g 1001 -S nodejs && \
    adduser -S vomesync -u 1001

# Install system dependencies
RUN apk add --no-cache dumb-init

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci --only=production && npm cache clean --force

# Copy application code
COPY src/ ./src/
COPY config/ ./config/ 2>/dev/null || true

# Create logs directory
RUN mkdir -p logs && chown -R vomesync:nodejs logs

# Switch to non-root user
USER vomesync

# Expose ports
EXPOSE 3000 3001

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD node -e "require('http').get('http://localhost:3000/api/health', (res) => { process.exit(res.statusCode === 200 ? 0 : 1) }).on('error', () => process.exit(1))"

# Use dumb-init to handle signals properly
ENTRYPOINT ["dumb-init", "--"]

# Start the application
CMD ["node", "src/server.js"]
