// scripts.js

function tryStatus() {
    fetch('/status')
        .then(response => response.json())
        .then(data => {
            document.getElementById('statusResult').innerText = JSON.stringify(data, null, 2);
        })
        .catch(error => {
            console.error('Error fetching /status:', error);
        });
}

function trySendText() {
    const phone = prompt('Enter phone number:');
    const text = prompt('Enter text:');
    const url = `/sendText?phone=${encodeURIComponent(phone)}&text=${encodeURIComponent(text)}`;

    fetch(url)
        .then(response => response.json())
        .then(data => {
            document.getElementById('sendTextResult').innerText = JSON.stringify(data, null, 2);
        })
        .catch(error => {
            console.error('Error fetching /sendText:', error);
        });
}

function tryCheckNumber() {
    const phone = prompt('Enter phone number:');
    const url = `/checkNumber?phone=${encodeURIComponent(phone)}`;

    fetch(url)
        .then(response => response.json())
        .then(data => {
            document.getElementById('checkNumberResult').innerText = JSON.stringify(data, null, 2);
        })
        .catch(error => {
            console.error('Error fetching /checkNumber:', error);
        });
}

function tryUnconnect() {
    fetch('/unconnect')
        .then(response => response.json())
        .then(data => {
            document.getElementById('unconnectResult').innerText = JSON.stringify(data, null, 2);
        })
        .catch(error => {
            console.error('Error fetching /unconnect:', error);
        });
}
