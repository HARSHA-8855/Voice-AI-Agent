import WebSocket from 'ws';
import express from 'express';

const app = express();
const server = app.listen(3000, () => {
    console.log('Gateway listening on port 3000');
});

const wss = new WebSocket.Server({ server });

wss.on('connection', (ws) => {
    console.log('New client connected');
    ws.on('message', (message) => {
        console.log('Received audio chunk');
    });
});
