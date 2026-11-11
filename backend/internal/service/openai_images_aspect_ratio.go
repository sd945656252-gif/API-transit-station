package service

import (
	"fmt"
	"strconv"
	"strings"
)

// aspect_ratio is a gateway extension. OAuth image endpoints have not been
// verified to accept a native ratio field; translate an explicit choice into
// prompt guidance, without claiming exact pixels or rewriting the result.
func normalizeOpenAIImagesAspectRatio(value string) (string, error) {
	parts := strings.Split(strings.TrimSpace(value), ":")
	if len(parts) != 2 {
		return "", fmt.Errorf("aspect_ratio must contain two positive integers as width:height")
	}
	dimensions := [2]int{}
	for i, part := range parts {
		part = strings.TrimSpace(part)
		if len(part) == 0 || len(part) > 5 {
			return "", fmt.Errorf("aspect_ratio components must be between 1 and 10000")
		}
		for _, digit := range part {
			if digit < '0' || digit > '9' {
				return "", fmt.Errorf("aspect_ratio must contain two positive integers as width:height")
			}
		}
		n, err := strconv.Atoi(part)
		if err != nil || n < 1 || n > 10000 {
			return "", fmt.Errorf("aspect_ratio components must be between 1 and 10000")
		}
		dimensions[i] = n
	}
	a, b := dimensions[0], dimensions[1]
	for b != 0 {
		a, b = b, a%b
	}
	return fmt.Sprintf("%d:%d", dimensions[0]/a, dimensions[1]/a), nil
}

func openAIImagesAspectRatioPrompt(prompt, ratio string) string {
	if ratio == "" {
		return prompt
	}
	return prompt + "\n\nThe final image canvas must have an aspect ratio of " + ratio +
		", with width:height = " + ratio +
		". Use the entire canvas. Do not place a frame of this shape inside an image with a different aspect ratio."
}
