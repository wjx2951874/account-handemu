// ecosystem.config.js
module.exports = {
  apps: [
    {
      name: 'account-handemu',
      script: './app.js',
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: '256M',
      env: {
        NODE_ENV: 'production'
      }
    }
  ]
};
