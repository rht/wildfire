/* Full-state transport: the backend owns ranking, facts and all operational changes. */
(function(root) {
  'use strict';
  class DashboardClient {
    constructor(options) {
      Object.assign(this, options);
      this.now = options.now || Date.now;
      this.setTimeout = options.setTimeout || root.setTimeout.bind(root);
      this.clearTimeout = options.clearTimeout || root.clearTimeout.bind(root);
      this.state = null;
      this.connection = 'connecting';
      this.stopped = false;
      this.generation = 0;
      this.retryDelay = 1000;
    }
    status() {
      const age = this.state ? this.now() - Date.parse(this.state.as_of) : Infinity;
      this.onStatus({connection:this.connection,
        stale:!Number.isFinite(age) || age > 120000 || this.state?.data_status === 'stale',
        errors:this.state?.errors?.length || 0});
    }
    accept(state, reset=false) {
      if (state?.schema_version !== 'coordination-state-1' || !Number.isInteger(state.revision) ||
          state.revision < 0 || !Array.isArray(state.assets) ||
          !Array.isArray(state.contacts?.ranked) || !Array.isArray(state.contacts?.review)) {
        throw new Error('invalid state');
      }
      if (!reset && this.state && state.revision <= this.state.revision) return;
      this.state = state;
      this.onState(state);
      this.status();
    }
    async start() {
      this.stopped = false;
      const generation = ++this.generation;
      this.connection = 'connecting';this.status();
      try {
        const state = await this.fetchState();
        if (this.stopped || generation !== this.generation) return;
        this.accept(state,true);
        this.socket = new this.WebSocket(`${this.socketUrl}?after_revision=${state.revision}`);
        const socket=this.socket;
        const active=()=>!this.stopped && generation===this.generation && this.socket===socket;
        const alive=()=>{
          this.clearTimeout(this.watchdog);
          this.watchdog=this.setTimeout(()=>{if(active()) this.recover('reconnecting');},25000);
        };
        socket.onopen=()=>{if(active()) {this.connection='connected';this.retryDelay=1000;this.status();alive();}};
        socket.onmessage=event=>{
          if(!active()) return;
          try {
            const message=JSON.parse(event.data);
            if(message.type==='error') {this.recover('unavailable');return;}
            if(message.type!=='heartbeat') this.accept(message);
            this.connection='connected';this.status();alive();
          } catch {this.recover('unavailable');}
        };
        socket.onclose=()=>{if(active()) this.recover('reconnecting');};
        socket.onerror=()=>{if(active()) this.recover('unavailable');};
        alive();
      } catch {if(generation===this.generation && !this.stopped) this.recover('unavailable');}
    }
    recover(connection) {
      ++this.generation;
      this.clearTimeout(this.watchdog);
      this.clearTimeout(this.retry);
      this.socket?.close();
      this.connection=connection;this.status();
      if(!this.stopped) {
        this.retry=this.setTimeout(()=>this.start(),this.retryDelay);
        this.retryDelay=Math.min(this.retryDelay*2,15000);
      }
    }
    stop() {
      this.stopped=true;++this.generation;
      this.clearTimeout(this.watchdog);this.clearTimeout(this.retry);this.socket?.close();
    }
  }
  if(typeof module!=='undefined' && module.exports) module.exports={DashboardClient};
  else root.DashboardClient=DashboardClient;
})(globalThis);
