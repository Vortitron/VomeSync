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
RUN npm ci --omit=dev && npm cache clean --force

# Copy application code
COPY src/ ./src/

# Copy config directory if it exists, otherwise create empty directory
COPY config* ./
RUN mkdir -p config

# Create logs + media directories
RUN mkdir -p logs media && chown -R vomesync:nodejs logs media

# Switch to non-root user
USER vomesync

# Expose ports
EXPOSE 3090 3001

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD node -e "require('http').get('http://localhost:3090/api/health', (res) => { process.exit(res.statusCode === 200 ? 0 : 1) }).on('error', () => process.exit(1))"

# Use dumb-init to handle signals properly
ENTRYPOINT ["dumb-init", "--"]

# Start the application
CMD ["node", "src/server.js"]
