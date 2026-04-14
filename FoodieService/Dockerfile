FROM node:20-alpine

# Install wget for healthcheck
RUN apk add --no-cache wget

# Install howtocook-mcp globally from npm (bundles all_recipes.json)
RUN npm install -g howtocook-mcp

EXPOSE 3000

CMD ["howtocook-mcp", "--transport", "http", "--port", "3000"]
