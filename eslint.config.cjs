module.exports = [{
  files: ['design/dashboard-*.js'],
  languageOptions: {
    ecmaVersion: 'latest', sourceType: 'script',
    globals: {document:'readonly', L:'readonly', module:'readonly', location:'readonly',
      fetch:'readonly', AbortSignal:'readonly'},
  },
  rules: {'no-unused-vars':'error', 'no-undef':'error', 'no-unreachable':'error',
    'no-constant-condition':'error', 'eqeqeq':['error','always'], 'no-eval':'error'},
}];
